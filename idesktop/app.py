"""One-click launcher: native iOS 27 mirror (HEVC stream + live touch + keyboard + audio) in an
Edge/Chrome app window, falling back to the classic WDA/Tk viewer on older iOS or on failure.

    python -m idesktop              # native if possible, else classic
    python -m idesktop --classic    # force the classic Tk viewer
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from . import device
from .device import CONFIG_DIR, DeviceManager, pmd, pmd_bg, tunneld_devices, usbmuxd_running

LOGS = CONFIG_DIR / "logs"
NATIVE_MIN_IOS = 27
CAMERA_BUSY = "camera-busy"


def msg(text: str, title: str = "iDesktop", error: bool = False, retry: bool = False) -> bool:
    """Show a message box (always: the launcher usually runs windowless). With retry=True,
    returns True when the user picks Retry."""
    print(text, flush=True)
    if os.name != "nt":
        return False
    flags = (0x10 if error else 0x40) | (0x05 if retry else 0)  # MB_ICONERROR/INFO | MB_RETRYCANCEL
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x40000) == 4  # topmost; IDRETRY


def single_instance() -> bool:
    """Named mutex: a second launch would open a second tunnel and knock out the first session."""
    if os.name != "nt":
        return True
    global _MUTEX  # keep the handle for the life of the process
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\idesktop-launcher")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


# ------------------------------------------------------------------ Apple service (iTunes)
def ensure_usbmuxd(log=print) -> bool:
    """The Microsoft Store iTunes only runs Apple Mobile Device Service while iTunes is open."""
    if usbmuxd_running():
        return True
    log("Starting Apple's device service so Windows can talk to the iPhone...")
    title = "iTunes"
    try:
        # The Store "Apple Devices" app and the Store iTunes both run the service only while open.
        found = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "foreach ($n in 'AppleInc.AppleDevices','AppleInc.iTunes') { $p = Get-AppxPackage $n; "
                                "if ($p) { $id = (Get-AppxPackageManifest $p).Package.Applications.Application.Id; "
                                "Write-Output \"$($p.PackageFamilyName)!$id\"; break } }"],
                               capture_output=True, text=True, timeout=30,
                               creationflags=device.CREATE_NO_WINDOW).stdout.strip().splitlines()
        if found:
            title = "Apple Devices" if "AppleDevices" in found[0] else "iTunes"
            os.startfile(f"shell:AppsFolder\\{found[0].split()[0]}")
        else:
            itunes = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "iTunes" / "iTunes.exe"
            if not itunes.exists():
                return False
            subprocess.Popen([str(itunes)])
    except Exception as e:
        log(f"Could not start iTunes: {e}")
        return False
    for _ in range(120):
        if usbmuxd_running():
            _minimize_window(title)
            return True
        time.sleep(0.5)
    return False


def _minimize_window(title: str) -> None:
    try:
        import win32con
        import win32gui
        for _ in range(10):
            h = win32gui.FindWindow(None, title)
            if h:
                win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
                return
            time.sleep(0.5)
    except Exception:
        pass


# ------------------------------------------------------------------ browser window
def find_browser() -> str | None:
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (fr"{pf86}\Microsoft\Edge\Application\msedge.exe", fr"{pf}\Microsoft\Edge\Application\msedge.exe",
              fr"{pf}\Google\Chrome\Application\chrome.exe", fr"{local}\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return shutil.which("msedge") or shutil.which("chrome")


def open_app_window(url: str) -> subprocess.Popen | None:
    exe = find_browser()
    if not exe:
        os.startfile(url)
        return None
    profile = CONFIG_DIR / "browser-profile"
    # Fit the window to the usable screen (above the taskbar) at a phone-ish aspect.
    h, top = 1160, 10
    try:
        from ctypes import wintypes
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100 or 1
        h = min(h, int((rect.bottom - rect.top) / scale) - 2 * top)
    except Exception:
        pass
    w = int(h * 0.52)
    # A dedicated profile means this process owns the window: it exits when the window closes.
    extra = []
    if os.environ.get("IDESKTOP_DEVTOOLS"):  # debugging only: tools/cdp.py talks to this port
        extra.append(f"--remote-debugging-port={os.environ['IDESKTOP_DEVTOOLS']}")
    return subprocess.Popen([exe, f"--app={url}", f"--window-size={w},{h}", f"--window-position=20,{top}",
                             f"--user-data-dir={profile}",
                             "--no-first-run", "--no-default-browser-check",
                             "--autoplay-policy=no-user-gesture-required", *extra])


# ------------------------------------------------------------------ native session
def _alive(url: str) -> bool:
    try:
        return requests.get(url, timeout=1.5).ok
    except Exception:
        return False


RECONNECT_EXIT = 3   # native.py exits with this when its tunnel to the phone dies


def run_native(udid: str, transport: list[str], wda_bundle: str | None, wifi_host: str | None = None,
               log=print, retarget=None) -> bool:
    """Run the HD stream in an app window. When the phone drops (Wi-Fi gone, cable pulled, phone
    restarted) the stream process exits and, while the window stays open, `retarget()` is polled
    for a fresh (transport, wifi_host) and the stream restarted on the same port; the page
    reconnects by itself."""
    procs: list[subprocess.Popen] = []
    port, ext_port, wda_port = device.free_port(8080), device.free_port(8091), device.free_port(8100)
    cwd = str(Path(__file__).resolve().parents[1])
    LOGS.mkdir(parents=True, exist_ok=True)

    def start_stream(tr: list[str], host: str | None) -> subprocess.Popen:
        # WDA is reached through the stream's own tunnel (the stream process relays it on
        # wda_port), so it keeps working over Wi-Fi after the cable is pulled.
        args = ["-m", "idesktop.native", "--port", str(port), "--ext-port", str(ext_port),
                "--wda-port", str(wda_port)]
        if wda_bundle:
            args += ["--wda-bundle", wda_bundle]
        if host:
            args += ["--wifi-host", host]  # used only if the USB route fails
        f = open(LOGS / "native.log", "w", encoding="utf-8", errors="replace")
        return subprocess.Popen([device._python(), *args, "--", *tr], stdout=f, stderr=subprocess.STDOUT,
                                env=device._env(), cwd=cwd, creationflags=device.CREATE_NO_WINDOW)

    try:
        hf = open(LOGS / "helper.log", "w", encoding="utf-8", errors="replace")
        procs.append(subprocess.Popen([device._python(), "-m", "idesktop.native", "--helper-only",
                                       "--ext-port", str(ext_port), "--wda-port", str(wda_port)],
                                      stdout=hf, stderr=subprocess.STDOUT, env=device._env(), cwd=cwd,
                                      creationflags=device.CREATE_NO_WINDOW))
        stream = start_stream(transport, wifi_host)
        procs.append(stream)
        url = f"http://127.0.0.1:{port}/"
        log("Starting the screen stream...")
        for _ in range(60):
            if stream.poll() is not None:
                tail = (LOGS / "native.log").read_text(errors="replace")[-1500:]
                log("Native stream failed to start:\n" + tail)
                if "camera or microphone is in use" in tail:
                    return CAMERA_BUSY
                return False
            if _alive(url):
                break
            time.sleep(0.5)
        else:
            return False
        log(f"Streaming at {url}")
        win = open_app_window(url)
        if win is None:
            # No dedicated browser process to watch; keep serving until the stream process dies.
            stream.wait()
            return True
        while win.poll() is None:
            time.sleep(1)
            if stream.poll() is None:
                continue
            log(f"Lost the phone (stream exit code {stream.returncode}); reconnecting...")
            nxt = None
            while win.poll() is None and nxt is None:
                try:
                    nxt = retarget() if retarget else (transport, wifi_host)
                except Exception as e:
                    log(f"reconnect: {e}")
                if nxt is None:
                    time.sleep(4)
            if nxt is None:
                break
            stream = start_stream(*nxt)
            procs.append(stream)
            for _ in range(40):  # don't spin if it dies straight away (e.g. phone still locked)
                if stream.poll() is not None or _alive(url):
                    break
                time.sleep(0.5)
            if stream.poll() is not None:
                time.sleep(4)
        return True
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


def pick_transport(dm: DeviceManager, log=print, quiet: bool = False) -> tuple[str, list[str], str] | None:
    """Returns (udid, pymobiledevice3 device args, iOS version) or None. quiet: no setup dialogs
    (reconnecting in the background) - just report "not yet"."""
    usb = []
    try:
        usb = dm.list_usb()
    except Exception as e:
        log(str(e))
    if usb:
        d = usb[0]
        udid = d.get("UniqueDeviceID") or d.get("Identifier")
        dm.cfg.update(udid=udid, product_type=d.get("ProductType"))
        device.save_config(dm.cfg)
        while True:
            try:
                device.prepare_usb_device(udid, log)
                break
            except device.SetupNeeded as e:
                if quiet:
                    log(f"reconnect: {e}")
                    return None
                if not msg(str(e), "iDesktop setup", retry=True):
                    raise SystemExit(0)
        pmd("lockdown", "wifi-connections", "on", "--udid", udid, timeout=20)
        device.ensure_wifi_pairing(udid)  # so the next start can be cable-free
        td = tunneld_devices() or {}
        tr = ["--tunnel", udid] if udid in td else ["--userspace", "--udid", udid]
        return udid, tr, str(d.get("ProductVersion", "0"))
    # No cable: the no-admin tunnel can still be dialled over Wi-Fi straight to the phone's last
    # known IP (native.patch_wifi_fallback); needs "Wi-Fi connections" on, set while plugged in.
    ios = str(dm.cfg.get("ios", "0"))
    if dm.cfg.get("udid") and ios.split(".")[0].isdigit() and int(ios.split(".")[0]) >= NATIVE_MIN_IOS:
        ip = device.find_phone_on_wifi(dm.cfg["udid"], dm.cfg.get("ip"), log)
        if ip:
            log(f"No USB iPhone found; connecting over Wi-Fi to {ip}...")
            dm.cfg["ip"] = ip
            device.save_config(dm.cfg)
            return dm.cfg["udid"], ["--userspace", "--udid", dm.cfg["udid"]], ios
    if quiet:
        return None  # never pop an admin prompt from a background reconnect
    # Older iOS: a running tunneld (admin) can reach the phone over Wi-Fi.
    td = tunneld_devices()
    if td is None and dm.cfg.get("udid"):
        log("No USB iPhone found; trying Wi-Fi (needs the admin tunnel)...")
        device.start_tunneld_elevated(log)
        for _ in range(20):
            td = tunneld_devices()
            if td:
                break
            time.sleep(1)
    if td:
        udid = dm.cfg.get("udid") if dm.cfg.get("udid") in td else next(iter(td))
        return udid, ["--tunnel", udid], str(dm.cfg.get("ios", "27"))
    return None


def main() -> None:
    if sys.stdout is None or sys.stderr is None:  # pythonw: keep a log instead of failing silently
        LOGS.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(LOGS / "launcher.log", "w", encoding="utf-8", errors="replace", buffering=1)
    try:
        _main()
    except Exception:
        import traceback
        traceback.print_exc()
        msg("iDesktop hit an error:\n\n" + traceback.format_exc()[-800:], error=True)


def _main() -> None:
    ap = argparse.ArgumentParser(description="Mirror and control an iPhone from Windows.")
    ap.add_argument("--classic", action="store_true", help="use the WDA/Tk viewer")
    a, rest = ap.parse_known_args()

    if not single_instance():
        msg("iDesktop is already running.")
        return
    if a.classic:
        sys.argv = [sys.argv[0], *rest]
        from .controller import main as classic
        return classic()

    if not ensure_usbmuxd():
        msg("Couldn't start Apple's device service. Open iTunes once, then try again.", error=True)
        return
    dm = DeviceManager(print)
    pick = pick_transport(dm)
    if not pick:
        msg("No iPhone found. Plug it in by USB, unlock it, and tap Trust.", error=True)
        return
    udid, transport, ios = pick
    dm.cfg["ios"] = ios
    device.save_config(dm.cfg)

    try:
        major = int(ios.split(".")[0])
    except ValueError:
        major = 0
    if major >= NATIVE_MIN_IOS:
        try:
            bundle = dm.find_wda_bundle(["--udid", udid]) if transport[0] != "--tunnel" else \
                dm.find_wda_bundle(transport)
        except Exception:
            bundle = None
        if bundle:
            dm.cfg["wda_bundle"] = bundle
            device.save_config(dm.cfg)
        else:
            bundle = dm.cfg.get("wda_bundle")  # no cable: the app lookup needs USB; reuse the last one
        def retarget():
            pick2 = pick_transport(dm, quiet=True)
            return (pick2[1], dm.cfg.get("ip")) if pick2 else None

        r = run_native(udid, transport, bundle, dm.cfg.get("ip"), retarget=retarget)
        if r is True:
            return
        if not bundle:
            # Basic mode is WebDriverAgent's screenshot stream; without the extra there's no fallback.
            why = ("iOS pauses HD mirroring while a call or an app is using the camera or microphone."
                   if r == CAMERA_BUSY else "HD mirroring failed to start (see logs\\native.log).")
            msg(why + "\n\nBasic mode (which also works during calls) needs the optional WebDriverAgent "
                "extra - see the README. Otherwise, try again after the call.", error=r != CAMERA_BUSY)
            return
        if r == CAMERA_BUSY:
            # iOS blocks the HD mirror while anything holds the camera/mic (calls, FaceTime...).
            # The WDA screenshot stream isn't subject to that, so keep working in basic mode.
            notice = ("HD mirroring is paused by iOS while a call or an app is using the camera/mic. "
                      "Using basic mode; reopen iDesktop after the call for HD.")
        else:
            notice = "HD mirroring failed to start (see logs\\native.log); using basic mode."
        rest = [*rest, "--notice", notice]
        print(notice)
    sys.argv = [sys.argv[0], "--autoconnect", *rest]
    from .controller import main as classic
    classic()


if __name__ == "__main__":
    main()
