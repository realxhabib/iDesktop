"""Native iOS 27+ mirroring: pymobiledevice3's CoreDevice screen stream with our layer on top.

Runs `pymobiledevice3 developer core-device display serve-web` in-process after patching it:
  * AAC-ELD audio decoded with FFmpeg (PyAV) - upstream only supports macOS AudioToolbox.
  * Viewer page gets a phone-first layout, wheel scrolling, system gestures, clipboard sync on,
    and pinch-to-zoom (Ctrl+wheel), which goes to WebDriverAgent because the native HID
    digitizer is single-touch.

Usage: python -m idesktop.native --port 8080 [--wda-port 8100] -- <pymobiledevice3 device args>
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("idesktop.native")


# ---------------------------------------------------------------------------- audio
class FFmpegAACELDDecoder:
    """Drop-in for pymobiledevice3's AudioToolbox decoder: one AAC-ELD AU -> s16le 48 kHz stereo PCM."""

    # The device's cookie (F8 E6 40 00) says frameLengthFlag=0 (512-sample frames) but the stream
    # is 480-sample ELD; AudioToolbox upstream pins 480 by hand. FFmpeg trusts the ASC, so flip the bit.
    ASC_480 = bytes([0xF8, 0xE6, 0x50, 0x00])
    SILENCE = bytes(480 * 4)

    def __init__(self, magic_cookie: bytes = b"") -> None:
        import av
        self._av = av
        self.ctx = av.CodecContext.create("aac", "r")
        self.ctx.extradata = self.ASC_480
        # FFmpeg's ER-AAC (ELD) path never fills in the output layout/rate itself, so every frame
        # fails its "invalid frame" sanity check (AVERROR_BUG) unless they are preset here.
        self.ctx.layout = "stereo"
        self.ctx.sample_rate = 48000
        self.ctx.open()
        self.res = av.AudioResampler(format="s16", layout="stereo", rate=48000)
        self._logged = False

    _dumped = 0

    def decode(self, au: bytes) -> bytes:
        import os
        dump = os.environ.get("IDESKTOP_AUDIO_DUMP")
        if dump and len(au) > 4 and FFmpegAACELDDecoder._dumped < 1500:
            FFmpegAACELDDecoder._dumped += 1
            with open(dump, "ab") as f:
                f.write(len(au).to_bytes(4, "big") + au)
        try:
            return self._decode(au)
        except Exception as e:
            if FFmpegAACELDDecoder._dumped < 400:
                FFmpegAACELDDecoder._dumped += 1
                log.warning("audio decode error %s: %s (len %d, head %s)", type(e).__name__, e, len(au), au[:12].hex())
            raise

    def _decode(self, au: bytes) -> bytes:
        if len(au) <= 4:  # the encoder's tiny "nothing playing" frames
            return self.SILENCE
        out = bytearray()
        for frame in self.ctx.decode(self._av.Packet(au)):
            if not self._logged:
                log.info("audio: FFmpeg AAC-ELD %d samples/frame @ %d Hz, %s",
                         frame.samples, frame.sample_rate, frame.layout.name)
                self._logged = True
            for r in self.res.resample(frame):
                out += bytes(r.planes[0])[: r.samples * 4]
        return bytes(out)


# ---------------------------------------------------------------------------- viewer layer
from . import viewer_layer  # HEAD_INJECT / BODY_INJECT live there (hot-reloaded)



class _Automation:
    """WebDriverAgent on/off. While it runs iOS shows its "Automation Running" overlay, and holding
    both volume buttons ends it - so that's honoured as "off" instead of relaunching, and the
    viewer can switch it on/off too. The choice is remembered in the config ("automation")."""
    available = False   # a WDA bundle is known
    wanted = True
    running = False
    run_task = None
    changed = None      # asyncio.Event, created on the stream's loop

    @classmethod
    def load(cls) -> None:
        from . import device
        cls.wanted = bool(device.load_config().get("automation", True))

    @classmethod
    def set(cls, on: bool) -> None:
        from . import device
        cls.wanted = on
        cfg = device.load_config()
        cfg["automation"] = on
        device.save_config(cfg)
        if cls.changed is not None:
            cls.changed.set()
        if not on and cls.run_task is not None:
            cls.run_task.cancel()   # ends the XCUITest session -> iOS removes the overlay

    @classmethod
    def state(cls) -> dict:
        return {"available": cls.available, "wanted": cls.wanted, "running": cls.running}


async def _keep_wda_running(server, bundle: str) -> None:
    """Run the WDA XCUITest runner on the stream's own tunnel (a second tunnel to the same phone
    tears the first one down). Relaunch it after a failure; leave it off when the user stopped it
    on the phone (it had been running fine) or switched it off in the viewer."""
    import asyncio
    from pymobiledevice3.services.dvt.testmanaged.xcuitest import TestConfig, XCUITestService

    A = _Automation
    A.available, A.changed = True, asyncio.Event()
    loop = asyncio.get_running_loop()
    while True:
        if not A.wanted:
            A.running = False
            A.changed.clear()
            await A.changed.wait()
            continue
        provider = server._rsd  # re-read: the server swaps in a fresh tunnel after reconnects
        t0 = loop.time()
        try:
            cfg = await TestConfig.create_for(provider, runner_bundle_id=bundle)
            log.info("launching WebDriverAgent (%s) on the stream tunnel", bundle)
            A.run_task = asyncio.create_task(XCUITestService(provider).run(cfg))
            A.running = True
            try:
                await A.run_task
            except asyncio.CancelledError:
                if A.run_task.cancelled() and not A.wanted:
                    log.info("WebDriverAgent switched off from the viewer")
                    continue
                raise
            finally:
                A.running, A.run_task = False, None
            if A.wanted and loop.time() - t0 > 20:
                log.info("WebDriverAgent was stopped on the phone; leaving automation off")
                A.set(False)
                continue
            log.warning("WebDriverAgent runner exited; relaunching")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("WebDriverAgent runner failed: %s: %s; retrying in 5s", type(e).__name__, e)
        await asyncio.sleep(5)


async def _control_server(port: int) -> None:
    """Tiny HTTP endpoint for the viewer: GET/POST /automation (CORS, text/plain JSON body)."""
    import asyncio

    async def handle(reader, writer) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, path = (lines[0].split() + ["", ""])[:2]
            length = next((int(l.split(":", 1)[1]) for l in lines[1:] if l.lower().startswith("content-length:")), 0)
            body = await reader.readexactly(length) if length else b""
            route = path.split("?")[0]
            if route == "/automation" and method == "POST":
                _Automation.set(bool(json.loads(body or b"{}").get("on")))
            if route == "/disconnect" and method == "POST":
                # End the stream (and WDA) but keep the window: the launcher waits for /reconnect.
                import os
                log.info("disconnect requested from the viewer")
                threading.Timer(0.3, lambda: os._exit(DISCONNECT_EXIT)).start()
            code = "200 OK" if route in ("/automation", "/status", "/disconnect") else "404 Not Found"
            if route == "/status":
                # iOS refuses to (re)start the stream during calls; upstream keeps retrying.
                now = time.time()
                out = json.dumps({"call_blocked": now - CALL_BLOCKED_AT < 45,
                                  # recent real stall (keyframe didn't help) or call block
                                  "stalled": now - max(STALLED_AT, CALL_BLOCKED_AT) < 45}).encode()
            else:
                out = json.dumps(_Automation.state()).encode()
            writer.write(f"HTTP/1.1 {code}\r\nAccess-Control-Allow-Origin: *\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(out)}\r\nConnection: close\r\n\r\n".encode() + out)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", port)
    async with srv:
        await srv.serve_forever()


CTL_PORT = 0   # set in patch_screen_stream; the viewer page gets it via build_html
MJPEG_PORT = 0   # local relay to WDA's MJPEG feed (device port 9100)
CALL_BLOCKED_AT = 0.0
STALLED_AT = 0.0        # last time a keyframe request failed to revive the video   # last time iOS refused the stream because of a call


async def _wda_relay(server, port: int, device_port: int = 8100) -> None:
    """127.0.0.1:<port> -> WebDriverAgent on the phone, through the stream's own CoreDevice tunnel.

    The tunnel survives losing the cable (it carries on over Wi-Fi), a usbmux port forward does
    not; and with --userspace the tunnel only exists inside this process, so relay it here."""
    import asyncio

    async def pump(reader, writer) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def handle(creader, cwriter) -> None:
        try:
            rsd = server._rsd  # re-read: rebound after reconnects
            opener = getattr(rsd, "open_connection", None) or asyncio.open_connection
            dreader, dwriter = await opener(rsd.service.address[0], device_port)
        except Exception as e:
            log.debug("WDA relay connect failed: %s", e)
            cwriter.close()
            return
        await asyncio.gather(pump(creader, dwriter), pump(dreader, cwriter))

    srv = await asyncio.start_server(handle, "127.0.0.1", port)
    log.info("WebDriverAgent relay on 127.0.0.1:%d (through the stream tunnel)", port)
    async with srv:
        await srv.serve_forever()


REMOTEPAIRING_PORTS = (49152, 49153, 49154, 49155, 49156)


def patch_wifi_fallback(host: str) -> None:
    """No cable: open the no-admin userspace tunnel over Wi-Fi, straight to the phone's IP.

    pymobiledevice3's no-root provider uses CoreDeviceProxy over a usbmux lockdown (USB only: the
    phone refuses that tunnel over the network) and otherwise looks the phone's RemotePairing
    service up by Bonjour, which needs multicast DNS that many home routers don't pass. So dial
    RemotePairing directly at <ip>:49152 (the usual port), using the pairing record created
    while plugged in (`lockdown remotepairing --pair`, see device.ensure_wifi_pairing)."""
    import socket

    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote import userspace_tunnel as ut

    orig = ut._create_no_root_tunnel_provider

    def find_port() -> int:
        for port in REMOTEPAIRING_PORTS:
            with socket.socket() as s:
                s.settimeout(2)
                if s.connect_ex((host, port)) == 0:
                    return port
        raise ConnectionError(f"the iPhone at {host} doesn't answer on Wi-Fi (RemotePairing port closed)")

    async def provider(serial, autopair, remotepairing_fallback=True):
        try:
            return await orig(serial, autopair, remotepairing_fallback)
        except Exception as e:
            log.info("no USB route to the phone (%s); connecting over Wi-Fi to %s", type(e).__name__, host)
        port = find_port()
        svc = await tunnel_service.create_core_device_tunnel_service_using_remotepairing(
            serial, host, port, autopair=False)
        return svc, None

    ut._create_no_root_tunnel_provider = provider


async def _mount_ddi(rsd) -> None:
    """The developer disk image is gone after every phone restart; mount it through the tunnel
    (works over Wi-Fi too). Already mounted is the normal case."""
    from pymobiledevice3.services.mobile_image_mounter import auto_mount
    try:
        await auto_mount(rsd)
        log.info("developer disk image mounted")
    except Exception as e:
        if "already" not in str(e).lower() and type(e).__name__ != "AlreadyMountedError":
            log.warning("developer disk image: %s: %s", type(e).__name__, e)


RECONNECT_EXIT = 3  # app.py restarts the stream (and finds the phone again) on this code
DISCONNECT_EXIT = 4  # the user disconnected: app.py waits for a reconnect request (helper /reconnect)
RECONNECT_FLAG = "reconnect.request"   # file in the config dir that the helper drops on /reconnect


def _exit_when_tunnel_dies() -> None:
    """With the userspace tunnel the stream can't recover by itself once the phone drops (Wi-Fi
    lost, cable pulled, phone restarted): exit so the launcher reconnects while the window stays."""
    import os

    class Watch(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            global CALL_BLOCKED_AT
            msg = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                msg += " " + str(record.exc_info[1])
            if "userspace tunnel transport closed" in msg or "userspace dial plane is closed" in msg:
                threading.Timer(1.0, lambda: os._exit(RECONNECT_EXIT)).start()
            if "code 9022" in msg or "camera or microphone is in use" in msg:
                CALL_BLOCKED_AT = time.time()   # the viewer explains it (see /status)
            return True

    for name in ("pymobiledevice3.remote.userspace_tunnel", "pymobiledevice3.remote.core_device.screen_stream",
                 "idesktop.native"):
        logging.getLogger(name).addFilter(Watch())


def patch_screen_stream(ext_port: int, wda_bundle: str | None = None, wda_port: int = 8100) -> None:
    import asyncio
    from pymobiledevice3.remote.core_device import screen_stream as ss

    global CTL_PORT, MJPEG_PORT
    from .device import free_port
    CTL_PORT = free_port(ext_port + 10)
    MJPEG_PORT = free_port(ext_port + 20)
    _Automation.load()
    ss.AACELDDecoder = FFmpegAACELDDecoder
    # iOS hides the on-screen keyboard while a hardware keyboard (ours) is attached; the
    # keyboard's Eject key (Consumer 0xB8) toggles it back, as on Apple's Magic Keyboard.
    ss._NAMED_BUTTONS.setdefault("keyboard", (0x0C, 0xB8, 0.05))
    # Keyboard brightness keys (Consumer 0x6F/0x70), which iOS obeys like a Magic Keyboard's. The
    # backlight is applied after screen capture, so the mirror stays bright while the phone dims.
    ss._NAMED_BUTTONS.setdefault("brightness-up", (0x0C, 0x6F, 0.05))
    ss._NAMED_BUTTONS.setdefault("brightness-down", (0x0C, 0x70, 0.05))
    # Upstream refuses /audio.bin off macOS because it assumes AudioToolbox; FFmpeg covers it here.
    ss.ScreenStreamServer._missing_audio_deps = staticmethod(lambda: [])
    orig_serve = ss.ScreenStreamServer.serve

    async def serve(self, *a, **kw):
        await _mount_ddi(self._rsd)
        self._ext_tasks = [asyncio.create_task(_wda_relay(self, wda_port)),
                           asyncio.create_task(_control_server(CTL_PORT)),
                           # WDA's own MJPEG screen feed: the viewer falls back to it while iOS
                           # sends no HD video (calls), then goes back to HD.
                           asyncio.create_task(_wda_relay(self, MJPEG_PORT, device_port=9100))]
        if wda_bundle:
            self._ext_tasks.append(asyncio.create_task(_keep_wda_running(self, wda_bundle)))
        return await orig_serve(self, *a, **kw)

    ss.ScreenStreamServer.serve = serve
    _exit_when_tunnel_dies()

    # Started during a call (camera/mic in use)? Upstream exits; instead serve anyway: the viewer
    # shows the floating phone with "paused during your call" (and the basic feed with
    # Automation), and the stream comes up lazily once iOS allows it again.
    orig_eager = ss.ScreenStreamServer._eager_stream_start
    in_use = getattr(ss, "is_media_in_use_error", None) or (lambda e: "9022" in str(e))

    async def eager_stream_start(self):
        global CALL_BLOCKED_AT
        try:
            await orig_eager(self)
        except Exception as e:
            if not in_use(e):
                raise
            CALL_BLOCKED_AT = time.time()
            log.info("camera/microphone in use (call?) - serving anyway; HD starts once iOS allows it")

    ss.ScreenStreamServer._eager_stream_start = eager_stream_start
    _patch_stall_watchdog(ss)
    upstream_html = ss.VIEWER_HTML
    ss.VIEWER_HTML = build_html(upstream_html, ext_port)
    orig_send = ss.ScreenStreamServer._send_static

    def send_static(writer, body, content_type):
        if content_type.startswith(b"text/html"):
            try:
                body = build_html(upstream_html, ext_port)  # pick up viewer_layer.py edits on reload
            except Exception:
                log.exception("viewer layer reload failed; serving the last good page")
        return orig_send(writer, body, content_type)

    ss.ScreenStreamServer._send_static = staticmethod(send_static)


_watchdog_fired_at = 0.0


def _patch_stall_watchdog(ss) -> None:
    """Upstream restarts the media stream whenever no frame arrives for 5 s -- which is what a
    perfectly healthy stream does on a still screen. Outside a call that restart is harmless;
    during a call iOS refuses to start a new stream ("A phone or VoIP call is currently in
    progress", code 9022), so HD dies the first time the screen sits still. Ask for a keyframe
    first and only fall through to the restart if that doesn't bring frames back."""
    import asyncio
    import time

    class _WatchdogTap(logging.Filter):
        def filter(self, record):
            global _watchdog_fired_at
            if str(record.msg).startswith("no AU progress"):
                _watchdog_fired_at = time.monotonic()
            return True

    ss.logger.addFilter(_WatchdogTap())
    orig = ss.ScreenStreamServer._ensure_fresh_stream

    async def ensure_fresh_stream(self, force: bool = False):
        global _watchdog_fired_at
        if force and time.monotonic() - _watchdog_fired_at < 1.0:
            _watchdog_fired_at = 0.0
            before = self._last_good_au_t
            for attempt in range(2):
                try:
                    self._fire_decoder_refresh(asyncio.get_running_loop().time(), reason="idle-check")
                except Exception:
                    log.debug("keyframe request failed", exc_info=True)
                for _ in range(20):
                    await asyncio.sleep(0.1)
                    if self._last_good_au_t != before:
                        log.info("stream was just idle (still screen); keyframe revived it - no restart")
                        self._consecutive_restarts = 0
                        return None
            global STALLED_AT
            STALLED_AT = time.time()   # a real stall, not just a still screen (see /status)
            log.warning("keyframe request didn't revive the stream; restarting it")
        return await orig(self, force)

    ss.ScreenStreamServer._ensure_fresh_stream = ensure_fresh_stream


def build_html(upstream: bytes, ext_port: int) -> bytes:
    import importlib
    layer = importlib.reload(viewer_layer)
    html = upstream.replace(b"</head>", layer.HEAD_INJECT.encode() + b"</head>", 1)
    body = (layer.BODY_INJECT.replace("__EXT_PORT__", str(ext_port)).replace("__CTL_PORT__", str(CTL_PORT))
            .replace("__MJPEG_PORT__", str(MJPEG_PORT)))
    html = html.replace(b"</body>", body.encode() + b"</body>", 1)
    return html.replace(b"<title>pymobiledevice3 screen</title>", b"<title>iDesktop</title>")


# ---------------------------------------------------------------------------- helper (pinch via WDA)
class Helper(BaseHTTPRequestHandler):
    wda_port = 8100
    _wda = None
    _lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _reply(self, code: int, body: str = "ok"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    @classmethod
    def wda(cls):
        from .wda import WDA
        with cls._lock:
            if cls._wda is None:
                w = WDA("127.0.0.1", cls.wda_port)
                w.new_session()
                w.refresh_size()
                cls._wda = w
            return cls._wda

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200)
        if self.path == "/orientation":
            return self._reply(200, json.dumps(Orientation.state))
        self._reply(404, "not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._reply(400, "bad json")
        if self.path == "/log":  # the viewer page reports its JS errors here
            log.warning("page: %s", str(body.get("msg"))[:800])
            return self._reply(200)
        if self.path.startswith("/window/"):
            from . import winshape
            if self.path != "/window/move":
                log.info("%s %s", self.path, json.dumps(body)[:300])
            try:
                if self.path == "/window/layout":
                    return self._reply(200, json.dumps(winshape.apply_layout(body)))
                if self.path == "/window/move":
                    winshape.move_by(body.get("dx", 0), body.get("dy", 0))
                    return self._reply(200)
                if self.path == "/window/cmd":
                    winshape.command(str(body.get("cmd")))
                    return self._reply(200)
            except Exception as e:
                return self._reply(503, f"{type(e).__name__}: {e}")
            return self._reply(404, "not found")
        if self.path == "/reconnect":
            # The stream process is gone while disconnected, so the launcher watches for this file.
            from . import device
            (device.CONFIG_DIR / RECONNECT_FLAG).write_text("1")
            return self._reply(200)
        if self.path == "/mjpeg":
            # Fallback feed quality (WDA applies the scaling factor squared: 75 -> ~56% size).
            try:
                self.wda().configure_stream(fps=24, scale=75, quality=60)
                return self._reply(200)
            except Exception:
                Helper._wda = None
                return self._reply(503, "needs WebDriverAgent - an optional extra, see the README")
        if self.path == "/volume":
            # "PC only" sound: the PC copy is taken before the phone's volume, but volume 0 mutes
            # it too, so step the phone down to the lowest audible step (1).
            try:
                w = self.wda()
                for _ in range(17):
                    w._s("POST", "/wda/pressButton", {"name": "volumeDown"})
                w._s("POST", "/wda/pressButton", {"name": "volumeUp"})
                return self._reply(200)
            except Exception:
                Helper._wda = None
                return self._reply(503, "needs WebDriverAgent - an optional extra, see the README")
        if self.path != "/pinch":
            return self._reply(404, "not found")
        try:
            w = self.wda()
            W, H = w.size
            w.pinch(float(body["x"]) * W, float(body["y"]) * H, float(body["scale"]))
            self._reply(200)
        except Exception:
            Helper._wda = None  # force a fresh session next time
            self._reply(503, "needs WebDriverAgent - an optional extra, see the README")


def _remember_wifi_ip(wda_port: int) -> None:
    """Keep the phone's current Wi-Fi IP in the config (WDA reports it), for cable-free starts."""
    import time

    import requests

    from . import device
    while True:
        try:
            ip = (requests.get(f"http://127.0.0.1:{wda_port}/status", timeout=5).json()
                  .get("value", {}).get("ios", {}).get("ip"))
            cfg = device.load_config()
            if ip and not ip.startswith(("127.", "169.254.", "172.20.10.")) and cfg.get("ip") != ip:
                cfg["ip"] = ip
                device.save_config(cfg)
                log.info("phone Wi-Fi IP is %s", ip)
        except Exception:
            pass
        time.sleep(60)


class Orientation(threading.Thread):
    """Tracks the foreground app's *interface* orientation for auto-rotate.

    The HD stream is always a portrait buffer; a landscape app is drawn sideways inside it, so the
    viewer must rotate. The phone's physical orientation is the wrong signal (a portrait-only app
    stays portrait when the phone is turned), so read the frontmost app's root frame from WDA
    (landscape when wider than tall) and use the device rotation only to pick left vs right."""

    state = {"landscape": False, "z": 0, "ok": False}

    def __init__(self, wda_port: int):
        super().__init__(daemon=True)
        self.wda_port = wda_port

    def run(self) -> None:
        import time
        from .wda import WDA
        w = None
        while True:
            try:
                if w is None:
                    w = WDA("127.0.0.1", self.wda_port, timeout=4)
                    w.new_session()
                    w._s("POST", "/appium/settings", {"settings": {"snapshotMaxDepth": 1}})
                root = w._s("GET", "/source?format=json", timeout=4) or {}
                r = root.get("rect") or {}
                z = int((w._s("GET", "/rotation", timeout=4) or {}).get("z", 0))
                Orientation.state = {"landscape": r.get("width", 0) > r.get("height", 0), "z": z, "ok": True}
                time.sleep(0.5)
            except Exception:
                Orientation.state = dict(Orientation.state, ok=False)
                w = None
                time.sleep(2)


def main() -> None:
    argv = sys.argv[1:]
    dev_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, dev_args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ext-port", type=int, default=8081)
    ap.add_argument("--wda-port", type=int, default=8100,
                    help="local port for WebDriverAgent (stream: relays it there; helper: uses it)")
    ap.add_argument("--wda-bundle", help="launch this WDA runner on the stream tunnel")
    ap.add_argument("--wifi-host", help="phone's Wi-Fi IP: used when there is no USB route")
    ap.add_argument("--helper-only", action="store_true",
                    help="run only the pinch/orientation helper (separate process, see main())")
    a = ap.parse_args(argv)

    # serve-web takes the device from the RSD options only; --udid is passed via its env var.
    if "--udid" in dev_args:
        i = dev_args.index("--udid")
        import os
        os.environ["PYMOBILEDEVICE3_UDID"] = dev_args[i + 1]
        del dev_args[i:i + 2]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not a.helper_only:
        patch_screen_stream(a.ext_port, a.wda_bundle, a.wda_port)
        if a.wifi_host:
            patch_wifi_fallback(a.wifi_host)
    if a.helper_only:
        # Separate process on purpose: with --userspace, pymobiledevice3 runs its own network
        # stack in the stream process, and plain localhost HTTP to WDA from there is unreliable.
        Helper.wda_port = a.wda_port
        Orientation(a.wda_port).start()
        threading.Thread(target=_remember_wifi_ip, args=(a.wda_port,), daemon=True).start()
        ThreadingHTTPServer(("127.0.0.1", a.ext_port), Helper).serve_forever()
        return

    from pymobiledevice3.__main__ import main as pmd_main
    sys.argv = ["pymobiledevice3", "developer", "core-device", "display", "serve-web",
                "--http-port", str(a.port), *dev_args]
    sys.exit(pmd_main())


if __name__ == "__main__":
    main()
