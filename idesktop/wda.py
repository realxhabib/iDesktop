"""Minimal WebDriverAgent client: session, MJPEG stream, touch gestures, buttons, typing.

All coordinates are in iOS points (the same space WDA's /window/size reports).
"""
from __future__ import annotations

import base64
import re
import threading
import time
from typing import Callable, Optional

import requests

Point = tuple[float, float]


class WDAError(RuntimeError):
    pass


class WDA:
    def __init__(self, host: str, port: int = 8100, mjpeg_port: int = 9100, timeout: float = 15.0):
        self.host = host
        self.port = port
        self.mjpeg_port = mjpeg_port
        self.timeout = timeout
        self.http = requests.Session()
        self.sid: Optional[str] = None
        self.size: Point = (390.0, 844.0)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- plumbing
    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def mjpeg_url(self) -> str:
        return f"http://{self.host}:{self.mjpeg_port}"

    def _req(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None):
        r = self.http.request(method, self.base + path, json=body, timeout=timeout or self.timeout)
        try:
            data = r.json()
        except ValueError:
            raise WDAError(f"{method} {path}: HTTP {r.status_code} (non-JSON reply)")
        val = data.get("value") if isinstance(data, dict) else None
        if r.status_code >= 400 or (isinstance(val, dict) and "error" in val):
            msg = val.get("message", "") if isinstance(val, dict) else ""
            err = val.get("error", r.status_code) if isinstance(val, dict) else r.status_code
            raise WDAError(f"{err}: {msg}".strip())
        return val

    def _s(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None):
        """Session-scoped request; transparently recreates a dead session once."""
        if not self.sid:
            self.new_session()
        try:
            return self._req(method, f"/session/{self.sid}{path}", body, timeout)
        except WDAError as e:
            if "invalid session" not in str(e).lower() and "session does not exist" not in str(e).lower():
                raise
            self.new_session()
            return self._req(method, f"/session/{self.sid}{path}", body, timeout)

    # ---------------------------------------------------------------- lifecycle
    def status(self, timeout: float = 3.0) -> dict:
        return self._req("GET", "/status", timeout=timeout) or {}

    def device_ip(self) -> Optional[str]:
        try:
            return (self.status().get("ios") or {}).get("ip")
        except Exception:
            return None

    def new_session(self) -> str:
        with self._lock:
            r = self.http.post(self.base + "/session",
                               json={"capabilities": {"alwaysMatch": {}, "firstMatch": [{}]}},
                               timeout=self.timeout)
            data = r.json()
            sid = data.get("sessionId") or (data.get("value") or {}).get("sessionId")
            if not sid:
                raise WDAError(f"could not create session: {data}")
            self.sid = sid
            return sid

    def configure_stream(self, fps: int = 30, scale: int = 50, quality: int = 50) -> None:
        self._s("POST", "/appium/settings", {"settings": {
            "mjpegServerFramerate": fps,
            "mjpegScalingFactor": scale,
            "mjpegServerScreenshotQuality": quality,
            "mjpegFixOrientation": True,
            # Don't wait for the UI to settle before injecting events: lower latency.
            "waitForIdleTimeout": 0,
            "animationCoolOffTimeout": 0,
        }})

    def refresh_size(self) -> Point:
        v = self._s("GET", "/window/size")
        self.size = (float(v["width"]), float(v["height"]))
        return self.size

    # ---------------------------------------------------------------- gestures
    def actions(self, steps: list[dict]) -> None:
        self._s("POST", "/actions", {"actions": [{
            "type": "pointer", "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": steps,
        }]}, timeout=30)

    def pinch(self, cx: float, cy: float, scale: float, ms: int = 350) -> None:
        """Two-finger pinch around (cx, cy). scale > 1 spreads fingers (zoom in), < 1 pinches (zoom out)."""
        w, h = self.size
        d0 = min(w, h) * (0.12 if scale >= 1 else 0.30)
        d1 = max(8.0, min(min(w, h) * 0.45, d0 * scale))

        def finger(fid: str, sign: int) -> dict:
            def at(d: float) -> dict:
                x, y = self._clamp(cx + sign * d * 0.7071, cy + sign * d * 0.7071)
                return {"x": round(x), "y": round(y)}
            return {"type": "pointer", "id": fid, "parameters": {"pointerType": "touch"}, "actions": [
                {"type": "pointerMove", "duration": 0, **at(d0)},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 30},
                {"type": "pointerMove", "duration": ms, **at(d1)},
                {"type": "pause", "duration": 60},
                {"type": "pointerUp", "button": 0},
            ]}
        self._s("POST", "/actions", {"actions": [finger("f1", -1), finger("f2", 1)]}, timeout=30)

    def tap(self, x: float, y: float) -> None:
        self.actions([
            {"type": "pointerMove", "duration": 0, "x": round(x), "y": round(y)},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 40},
            {"type": "pointerUp", "button": 0},
        ])

    def path(self, pts: list[tuple[float, float, float]], hold_end_ms: int = 0) -> None:
        """Replay a finger path. pts = [(t_ms, x, y), ...] with t relative to the first point."""
        if not pts:
            return
        t0, x0, y0 = pts[0]
        steps = [{"type": "pointerMove", "duration": 0, "x": round(x0), "y": round(y0)},
                 {"type": "pointerDown", "button": 0}]
        prev_t = t0
        for t, x, y in pts[1:]:
            steps.append({"type": "pointerMove", "duration": max(1, int(t - prev_t)),
                          "x": round(x), "y": round(y)})
            prev_t = t
        if hold_end_ms > 0:
            steps.append({"type": "pause", "duration": int(hold_end_ms)})
        steps.append({"type": "pointerUp", "button": 0})
        self.actions(steps)

    def swipe(self, a: Point, b: Point, ms: int = 250, hold_start_ms: int = 0, hold_end_ms: int = 0) -> None:
        steps = [{"type": "pointerMove", "duration": 0, "x": round(a[0]), "y": round(a[1])},
                 {"type": "pointerDown", "button": 0}]
        if hold_start_ms:
            steps.append({"type": "pause", "duration": hold_start_ms})
        steps.append({"type": "pointerMove", "duration": ms, "x": round(b[0]), "y": round(b[1])})
        if hold_end_ms:
            steps.append({"type": "pause", "duration": hold_end_ms})
        steps.append({"type": "pointerUp", "button": 0})
        self.actions(steps)

    # ---------------------------------------------------------------- system gestures
    def _clamp(self, x: float, y: float) -> Point:
        w, h = self.size
        return (min(max(x, 1), w - 1), min(max(y, 1), h - 1))

    def back(self, y: Optional[float] = None) -> None:
        w, h = self.size
        y = h / 2 if y is None else y
        self.swipe((1, y), (w * 0.7, y), ms=220)

    def app_switcher(self, home_button: bool = False) -> None:
        if home_button:
            self.press("home"); time.sleep(0.12); self.press("home")
            return
        w, h = self.size
        self.swipe((w / 2, h - 2), (w / 2, h * 0.62), ms=350, hold_end_ms=700)

    def go_home_gesture(self) -> None:
        w, h = self.size
        self.swipe((w / 2, h - 2), (w / 2, h * 0.45), ms=180)

    def control_center(self, home_button: bool = False) -> None:
        w, h = self.size
        if home_button:
            self.swipe((w / 2, h - 2), (w / 2, h * 0.4), ms=300)
        else:
            self.swipe((w - 30, 3), (w - 30, h * 0.5), ms=300)

    def notifications(self) -> None:
        w, h = self.size
        self.swipe((w * 0.3, 3), (w * 0.3, h * 0.6), ms=300)

    def spotlight(self) -> None:
        w, h = self.size
        self.swipe((w / 2, h * 0.35), (w / 2, h * 0.6), ms=250)

    # ---------------------------------------------------------------- buttons & keys
    def home(self) -> None:
        self._req("POST", "/wda/homescreen")

    def press(self, name: str) -> None:
        """name: home | volumeUp | volumeDown"""
        self._s("POST", "/wda/pressButton", {"name": name})

    def lock(self) -> None:
        self._req("POST", "/wda/lock")

    def unlock(self) -> None:
        self._req("POST", "/wda/unlock")

    def is_locked(self) -> bool:
        return bool(self._req("GET", "/wda/locked"))

    def type_text(self, text: str) -> None:
        self._s("POST", "/wda/keys", {"value": list(text)}, timeout=60)

    def set_pasteboard(self, text: str) -> None:
        self._s("POST", "/wda/setPasteboard", {
            "content": base64.b64encode(text.encode("utf-8")).decode(), "contentType": "plaintext"})

    def screenshot_png(self) -> bytes:
        return base64.b64decode(self._req("GET", "/screenshot", timeout=30))

    def launch_app(self, bundle_id: str) -> None:
        self._s("POST", "/wda/apps/launch", {"bundleId": bundle_id})


_CLEN = re.compile(rb"content-length:\s*(\d+)", re.I)


def _pop_frames(buf: bytearray) -> Optional[bytes]:
    """Consume every complete multipart part in buf; return the newest JPEG (older ones are stale)."""
    latest = None
    while True:
        hdr_end = buf.find(b"\r\n\r\n")
        if hdr_end < 0:
            return latest
        m = _CLEN.search(buf, 0, hdr_end)
        if m:
            start = hdr_end + 4
            n = int(m.group(1))
            if len(buf) < start + n:
                return latest
            latest = bytes(buf[start:start + n])
            del buf[:start + n]
            continue
        # No Content-Length header: fall back to JPEG markers.
        soi = buf.find(b"\xff\xd8")
        if soi < 0:
            del buf[:hdr_end + 4]
            continue
        eoi = buf.find(b"\xff\xd9", soi + 2)
        if eoi < 0:
            return latest
        latest = bytes(buf[soi:eoi + 2])
        del buf[:eoi + 2]


class MJPEGReader(threading.Thread):
    """Pulls frames from WDA's MJPEG server and hands the newest JPEG bytes to on_frame."""

    def __init__(self, url: str, on_frame: Callable[[bytes], None], on_state: Callable[[str], None]):
        super().__init__(daemon=True)
        self.url = url
        self.on_frame = on_frame
        self.on_state = on_state
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.on_state("connecting stream")
                with requests.get(self.url, stream=True, timeout=(5, 10)) as r:
                    r.raise_for_status()
                    self.on_state("streaming")
                    buf = bytearray()
                    raw = r.raw
                    while True:
                        # read1 returns as soon as *any* bytes arrive; iter_content would block
                        # until a full chunk, delaying (and dropping) frames.
                        chunk = raw.read1(262144)
                        if not chunk:
                            raise ConnectionError("stream ended")
                        if self._stop.is_set():
                            return
                        buf += chunk
                        frame = _pop_frames(buf)
                        if frame is not None:
                            self.on_frame(frame)
                        if len(buf) > 16_000_000:
                            buf.clear()
            except Exception as e:  # network blips, WDA restarts, unplug
                if self._stop.is_set():
                    return
                self.on_state(f"stream lost ({type(e).__name__}); retrying")
                self._stop.wait(1.5)
