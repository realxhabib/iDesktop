"""Device-side orchestration via the pymobiledevice3 CLI.

Wired (USB):   userspace iOS-17 tunnel (no admin) -> mount DDI -> launch WDA via XCUITest
               -> usbmux port-forward 8100/9100 to localhost.
Wireless:      if WDA is already up, talk straight to <phone-ip>:8100.
               Otherwise use an elevated `tunneld` (which finds the phone over Wi-Fi)
               to mount + launch WDA, then talk to <phone-ip>:8100.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import requests

Log = Callable[[str], None]

TUNNELD = "http://127.0.0.1:49151"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "iDesktop"
_OLD_CONFIG_DIR = CONFIG_DIR.parent / "iphone-controller"   # pre-release name: carry the config over
if not (CONFIG_DIR / "config.json").exists() and (_OLD_CONFIG_DIR / "config.json").exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "config.json").write_bytes((_OLD_CONFIG_DIR / "config.json").read_bytes())
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _python() -> str:
    # Prefer console python (pythonw swallows output we need to parse).
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def _env() -> dict:
    e = dict(os.environ)
    e.update({"PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "COLUMNS": "200", "TERM": "dumb"})
    return e


def pmd(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([_python(), "-m", "pymobiledevice3", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=_env(),
                          creationflags=CREATE_NO_WINDOW)


def pmd_bg(*args: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen([_python(), "-m", "pymobiledevice3", *args], stdout=f, stderr=subprocess.STDOUT,
                            env=_env(), creationflags=CREATE_NO_WINDOW)


def _json_out(cp: subprocess.CompletedProcess):
    out = cp.stdout.strip()
    i = min([p for p in (out.find("["), out.find("{")) if p >= 0], default=-1)
    if i < 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip()[-600:] or "no output")
    return json.loads(out[i:])


def _tail(cp: subprocess.CompletedProcess, n: int = 600) -> str:
    return ((cp.stderr or "") + (cp.stdout or "")).strip()[-n:]


def free_port(preferred: int) -> int:
    for p in [preferred] + list(range(preferred + 1, preferred + 50)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free local port")


def usbmuxd_running() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 27015)) == 0


class SetupNeeded(Exception):
    """A one-time step the user has to do on the phone; the message says what."""


def prepare_usb_device(udid: str, log: Log = print) -> None:
    """First-run checks over USB: trusted, Developer Mode on, developer disk image mounted.
    Raises SetupNeeded with instructions when the user has to act on the phone."""
    cp = pmd("lockdown", "info", "--udid", udid, timeout=40)   # pairs on first contact
    out = _tail(cp)
    if cp.returncode != 0:
        if any(k in out for k in ("PasswordProtected", "PairingDialogResponsePending", "UserDeniedPairing",
                                  "NotPaired", "Pairing")):
            raise SetupNeeded("Unlock your iPhone and tap \"Trust\" (enter your passcode if asked), "
                              "then click Retry.\n\nIf you tapped \"Don't Trust\": unplug, then on the "
                              "iPhone go to Settings > General > Transfer or Reset iPhone > Reset > "
                              "Reset Location & Privacy, and plug in again.")
        raise SetupNeeded("Couldn't talk to the iPhone:\n\n" + out[-600:])
    dm = DeviceManager(log).developer_mode(["--udid", udid])
    if dm is False:
        pmd("amfi", "reveal-developer-mode", "--udid", udid, timeout=30)
        raise SetupNeeded("Turn on Developer Mode (one time):\n\n"
                          "1. On the iPhone: Settings > Privacy & Security > Developer Mode > On.\n"
                          "2. The iPhone restarts; after it does, unlock it and tap \"Turn On\".\n"
                          "3. Click Retry.")
    log("Preparing developer services (the first run downloads ~20 MB)...")
    cp = pmd("mounter", "auto-mount", "--udid", udid, timeout=300)
    if cp.returncode != 0 and "already" not in _tail(cp).lower():
        raise SetupNeeded("Couldn't mount the developer disk image (is the iPhone unlocked?):\n\n"
                          + _tail(cp)[-600:])


def _lan_hosts() -> list[str]:
    """Every other address on this PC's IPv4 /24 networks (skips link-local, loopback and
    VPN-style /32s). On an iPhone hotspot (172.20.10.x, over Wi-Fi or USB) the phone is always
    172.20.10.1, so that's the only address worth trying there."""
    import ipaddress
    cp = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { \"$($_.IPAddress)/$($_.PrefixLength)\" }"],
                        capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    hosts: list[str] = []
    for line in cp.stdout.split():
        try:
            itf = ipaddress.ip_interface(line.strip())
        except ValueError:
            continue
        ip = itf.ip
        if ip.is_loopback or ip.is_link_local or itf.network.prefixlen > 30:
            continue
        if str(ip).startswith("172.20.10."):
            hosts.insert(0, "172.20.10.1")   # the phone itself, when the PC is on its hotspot
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)  # don't sweep huge networks
        hosts += [str(h) for h in net.hosts() if h != ip]
    return hosts


def _is_our_phone(host: str, udid: str) -> bool:
    """Lockdown over Wi-Fi (port 62078) with our pair record: only the paired phone answers with its UDID."""
    import asyncio

    async def check() -> bool:
        from pymobiledevice3.common import get_home_folder
        from pymobiledevice3.lockdown import create_using_tcp
        from pymobiledevice3.pair_records import get_preferred_pair_record
        record = await get_preferred_pair_record(udid, get_home_folder())
        ld = await asyncio.wait_for(create_using_tcp(host, identifier=udid, autopair=False, pair_record=record), 8)
        try:
            return ld.udid == udid
        finally:
            await ld.close()
    try:
        return asyncio.run(check())
    except Exception:
        return False


def find_phone_on_wifi(udid: str, hint: Optional[str] = None, log: Log = print) -> Optional[str]:
    """The paired iPhone's current Wi-Fi IP: the last known one if it still answers, else a quick
    sweep of the local network for Apple's lockdown port, confirmed with the pair record."""
    from concurrent.futures import ThreadPoolExecutor

    def open_port(host: str) -> Optional[str]:
        with socket.socket() as s:
            s.settimeout(0.6)
            return host if s.connect_ex((host, 62078)) == 0 else None

    if hint and open_port(hint) and _is_our_phone(hint, udid):
        return hint
    log("Looking for the iPhone on your Wi-Fi...")
    with ThreadPoolExecutor(64) as pool:
        cands = [h for h in pool.map(open_port, _lan_hosts()) if h and h != hint]
    for h in cands:
        if _is_our_phone(h, udid):
            return h
    return None


def ensure_wifi_pairing(udid: str) -> bool:
    """Create the RemotePairing record used for cable-free (Wi-Fi) starts, once, over USB.
    Promptless: it rides the already-trusted USB pairing."""
    try:
        from pymobiledevice3.common import get_home_folder
        if (get_home_folder() / f"remote_{udid}.plist").exists():
            return True
    except Exception:
        pass
    cp = pmd("lockdown", "remotepairing", "--pair", "--udid", udid, timeout=60)
    return cp.returncode == 0


def tunneld_devices() -> Optional[dict]:
    try:
        return requests.get(TUNNELD + "/", timeout=1.5).json()
    except Exception:
        return None


def wda_alive(host: str, port: int = 8100, timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"http://{host}:{port}/status", timeout=timeout)
        return r.ok
    except Exception:
        return False


def start_tunneld_elevated(log: Log) -> bool:
    """Launch `pymobiledevice3 remote tunneld` as Administrator (UAC prompt)."""
    if tunneld_devices() is not None:
        return True
    log("Starting tunneld as Administrator (approve the UAC prompt)...")
    params = '-m pymobiledevice3 remote tunneld --wifi --usb'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", _python(), params, None, 7)  # 7 = minimized
    if rc <= 32:
        log("UAC was declined or failed; tunneld not started.")
        return False
    for _ in range(40):
        if tunneld_devices() is not None:
            log("tunneld is up.")
            return True
        time.sleep(0.5)
    log("tunneld did not come up within 20s.")
    return False


class DeviceManager:
    def __init__(self, log: Log):
        self.log = log
        self.procs: list[subprocess.Popen] = []
        self.cfg = load_config()
        self.logdir = CONFIG_DIR / "logs"

    # ------------------------------------------------------------------ discovery
    def list_usb(self) -> list[dict]:
        if not usbmuxd_running():
            raise RuntimeError("Apple Mobile Device Service isn't running. Plug the iPhone in and open "
                               "iTunes (or the Apple Devices app) once so Windows starts it.")
        devs = _json_out(pmd("usbmux", "list", "--usb", timeout=30))
        return [d for d in devs if isinstance(d, dict)]

    def find_wda_bundle(self, transport: list[str]) -> str:
        cp = pmd("apps", "list", "--type", "User", *transport, timeout=60)
        apps = _json_out(cp)
        cands = [bid for bid, info in apps.items()
                 if "WebDriverAgentRunner" in bid or
                 str(info.get("CFBundleExecutable", "")).startswith("WebDriverAgentRunner")]
        cands.sort(key=lambda b: (not b.endswith(".xctrunner"), b))
        if not cands:
            raise RuntimeError("WebDriverAgent isn't installed on the phone. It's an optional extra: run "
                               "extras\\install-wda.ps1 and sideload the .ipa it builds (see README).")
        return cands[0]

    def developer_mode(self, transport: list[str]) -> Optional[bool]:
        cp = pmd("amfi", "developer-mode-status", *transport, timeout=30)
        out = (cp.stdout or "").strip().lower()
        if "true" in out:
            return True
        if "false" in out:
            return False
        return None

    # ------------------------------------------------------------------ launch
    def _prepare(self, transport: list[str], wired_udid: Optional[str]) -> str:
        dm = self.developer_mode(["--udid", wired_udid] if wired_udid else transport)
        if dm is False:
            pmd("amfi", "reveal-developer-mode", *(["--udid", wired_udid] if wired_udid else transport))
            raise RuntimeError("Developer Mode is OFF. On the iPhone: Settings > Privacy & Security > "
                               "Developer Mode > On, then restart the phone and try again.")

        bundle = self.find_wda_bundle(["--udid", wired_udid] if wired_udid else transport)
        self.log(f"WebDriverAgent bundle: {bundle}")

        self.log("Mounting Developer Disk Image (first run downloads ~20 MB)...")
        cp = pmd("mounter", "auto-mount", *transport, timeout=300)
        if cp.returncode != 0 and "already" not in _tail(cp).lower():
            raise RuntimeError("DDI mount failed:\n" + _tail(cp))
        return bundle

    def _launch_wda(self, bundle: str, transport: list[str]) -> None:
        self.log("Launching WebDriverAgent (XCUITest)...")
        p = pmd_bg("developer", "dvt", "xcuitest", bundle, *transport,
                   log_path=self.logdir / "xcuitest.log")
        self.procs.append(p)

    def _wait(self, host: str, port: int, proc_watch: Optional[subprocess.Popen], secs: float = 75) -> None:
        t0 = time.time()
        while time.time() - t0 < secs:
            if wda_alive(host, port):
                return
            if proc_watch is not None and proc_watch.poll() is not None:
                tail = (self.logdir / "xcuitest.log").read_text(errors="replace")[-1500:]
                raise RuntimeError("WebDriverAgent exited during launch. If the phone says "
                                   "'Untrusted Developer', go to Settings > General > VPN & Device "
                                   "Management and trust your Apple ID.\n\n" + tail)
            time.sleep(1)
        raise RuntimeError(f"WebDriverAgent did not answer on {host}:{port} within {int(secs)}s. "
                           f"See {self.logdir / 'xcuitest.log'}")

    def connect_usb(self) -> tuple[str, int, int]:
        devs = self.list_usb()
        if not devs:
            raise RuntimeError("No iPhone on USB. Plug it in, unlock it and tap 'Trust This Computer'.")
        d = devs[0]
        udid = d.get("UniqueDeviceID") or d.get("Identifier")
        self.log(f"Found {d.get('DeviceName', 'iPhone')} - iOS {d.get('ProductVersion', '?')} "
                 f"({d.get('ProductType', '?')})")
        self.cfg.update(udid=udid, product_type=d.get("ProductType"))

        # Enable Wi-Fi connections so tunneld can find the phone later without the cable.
        pmd("lockdown", "wifi-connections", "on", "--udid", udid, timeout=20)

        if wda_alive("127.0.0.1", 8100):  # left running by a previous --keep-wda session
            self.log("WebDriverAgent already reachable on localhost.")
            save_config(self.cfg)
            return "127.0.0.1", 8100, 9100
        lport, mport = free_port(8100), free_port(9100)
        td = tunneld_devices()
        transport = ["--tunnel", udid] if td and udid in td else ["--userspace", "--udid", udid]
        self.log("Transport: " + ("tunneld" if transport[0] == "--tunnel" else "userspace tunnel (no admin)"))

        # Forward first so we can poll WDA as soon as it boots.
        for lp, rp in ((lport, 8100), (mport, 9100)):
            self.procs.append(pmd_bg("usbmux", "forward", str(lp), str(rp), "--udid", udid,
                                     log_path=self.logdir / f"forward-{rp}.log"))
        time.sleep(1)
        if not wda_alive("127.0.0.1", lport):  # WDA may still be running on the phone from earlier
            bundle = self._prepare(transport, udid)
            self._launch_wda(bundle, transport)
            self._wait("127.0.0.1", lport, self.procs[-1])
        self.log("WebDriverAgent is up over USB.")
        save_config(self.cfg)
        return "127.0.0.1", lport, mport

    def connect_wifi(self, ip: Optional[str]) -> tuple[str, int, int]:
        ip = (ip or self.cfg.get("ip") or "").strip()
        if ip and wda_alive(ip, 8100):
            self.log(f"WebDriverAgent already running at {ip}.")
            return ip, 8100, 9100

        # Need to (re)launch WDA without a cable -> tunneld over Wi-Fi.
        if not start_tunneld_elevated(self.log):
            raise RuntimeError("Wireless launch needs tunneld (admin). Or connect once over USB first: "
                               "WDA keeps running and you can then switch to Wi-Fi.")
        udid = self.cfg.get("udid")
        devs = {}
        for _ in range(30):
            devs = tunneld_devices() or {}
            if devs:
                break
            time.sleep(1)
        if not devs:
            raise RuntimeError("tunneld can't see the phone over Wi-Fi. Make sure it's unlocked, on the same "
                               "network, and has been connected over USB at least once with this app.")
        if udid not in devs:
            udid = next(iter(devs))
        transport = ["--tunnel", udid]
        self.log(f"Phone found via tunneld ({udid[:8]}...)")
        bundle = self._prepare(transport, None)
        self._launch_wda(bundle, transport)
        if not ip:
            # The tunnel address is a private IPv6 link, not the phone's LAN IP, so we can't derive it here.
            raise RuntimeError("WDA launched, but the phone's Wi-Fi IP is unknown. Enter it "
                               "(Settings > Wi-Fi > (i) > IP Address) and press Connect again.")
        self._wait(ip, 8100, self.procs[-1])
        return ip, 8100, 9100

    def remember_ip(self, ip: Optional[str]) -> None:
        if ip:
            self.cfg["ip"] = ip
            save_config(self.cfg)

    def shutdown(self) -> None:
        """Stops forwards and the XCUITest host (which also stops WDA on the phone)."""
        for p in self.procs:
            try:
                p.terminate()
            except Exception:
                pass
        self.procs.clear()
