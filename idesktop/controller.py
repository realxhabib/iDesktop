"""iDesktop: mirror + full touch/keyboard control over USB or Wi-Fi."""
from __future__ import annotations

import argparse
import io
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from .device import DeviceManager, load_config
from .wda import WDA, MJPEGReader

# name: (fps, mjpeg scale %, jpeg quality). WDA applies the scale factor squared on iOS 27
# (60 -> 36% width), so 55/75/100 give roughly 30% / 56% / 100% of native width.
QUALITY = {
    "Smooth": (30, 55, 45),
    "Balanced": (30, 75, 55),
    "Sharp": (24, 100, 70),
}

# Phones with a physical Home button (everything else uses Face ID gestures).
HOME_BUTTON_MODELS = {"iPhone8,4", "iPhone12,8", "iPhone14,6", "iPhone10,1", "iPhone10,4", "iPhone10,2",
                      "iPhone10,5"}

TAP_SLOP_PT = 8
TAP_MAX_MS = 300

HELP = """Mouse
  click        tap
  hold         long-press
  drag         swipe / drag
  wheel        scroll (Shift = sideways)
  right-click  back (edge swipe)
  middle       Home

Keyboard (click phone first)
  type         types into focused field
  Ctrl+V       paste PC clipboard
  Home / F1    Home
  F2           App switcher
  F3           Control Center
  F4           Notifications
  F5           Spotlight
  PgUp/PgDn    Volume
  F12          Screenshot"""


def has_home_button(product_type: str | None) -> bool:
    if not product_type:
        return False
    if product_type in HOME_BUTTON_MODELS:
        return True
    try:
        major = int(product_type.replace("iPhone", "").split(",")[0])
    except ValueError:
        return False
    return major < 10


class App(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.title("iDesktop")
        self.geometry("760x980")
        self.minsize(520, 600)
        self.configure(bg="#111")
        self.args = args

        self.cfg = load_config()
        self.dm = DeviceManager(self.log_threadsafe)
        self.wda: WDA | None = None
        self.reader: MJPEGReader | None = None

        self._ui_q: queue.Queue = queue.Queue()
        self._cmd_q: queue.Queue = queue.Queue()
        self._pending = None          # (PhotoImage-ready PIL image, disp tuple)
        self._canvas_wh = (500, 900)
        self.disp = None              # (ox, oy, dw, dh, fw, fh)
        self.photo = None
        self._frames = 0
        self._fps = 0.0
        self._fps_t = time.time()
        self._last_size_check = 0.0
        self._gesture: list[tuple[float, float, float]] | None = None
        self._g_t0 = 0.0
        self._touch_dot = None
        self._wheel_acc = 0
        self._wheel_pos = (0, 0)
        self._wheel_shift = False
        self._wheel_job = None
        self._typed = ""
        self._type_job = None
        self._stream_state = "idle"

        self._build_ui()
        threading.Thread(target=self._cmd_worker, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(15, self._tick)

        if args.ip:
            self.mode.set("Wi-Fi (wireless)")
            self.ip_var.set(args.ip)
        if getattr(args, "notice", None):
            self._log(args.notice)
            self.title("iDesktop (Lite mode)")
        if args.ip or args.autoconnect:
            self.after(200, self.connect)

    # ================================================================== UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background="#1b1b1b", foreground="#e8e8e8", fieldbackground="#2a2a2a")
        style.configure("TButton", padding=(8, 5), background="#2d2d2d")
        style.map("TButton", background=[("active", "#3d3d3d")])
        style.configure("Accent.TButton", background="#0a84ff", foreground="white")
        style.map("Accent.TButton", background=[("active", "#3a9bff")])
        style.configure("TCheckbutton", background="#1b1b1b")
        style.map("TCombobox", fieldbackground=[("readonly", "#2a2a2a")],
                  foreground=[("readonly", "#e8e8e8")], selectbackground=[("readonly", "#2a2a2a")],
                  selectforeground=[("readonly", "#e8e8e8")])
        self.option_add("*TCombobox*Listbox.background", "#2a2a2a")
        self.option_add("*TCombobox*Listbox.foreground", "#e8e8e8")
        style.configure("Side.TFrame", background="#1b1b1b")

        top = ttk.Frame(self, padding=6)
        top.pack(side="top", fill="x")
        self.mode = tk.StringVar(value="Wi-Fi (wireless)" if self.cfg.get("mode") == "wifi" else "USB (wired)")
        ttk.Combobox(top, textvariable=self.mode, values=["USB (wired)", "Wi-Fi (wireless)"], width=16,
                     state="readonly").pack(side="left")
        ttk.Label(top, text=" IP").pack(side="left")
        self.ip_var = tk.StringVar(value=self.cfg.get("ip", ""))
        ttk.Entry(top, textvariable=self.ip_var, width=15).pack(side="left", padx=(2, 6))
        self.btn_connect = ttk.Button(top, text="Connect", style="Accent.TButton", command=self.connect)
        self.btn_connect.pack(side="left")
        ttk.Button(top, text="Disconnect", command=self.disconnect).pack(side="left", padx=4)
        self.quality = tk.StringVar(value=self.cfg.get("quality", "Balanced"))
        q = ttk.Combobox(top, textvariable=self.quality, values=list(QUALITY), width=9, state="readonly")
        q.pack(side="left", padx=4)
        q.bind("<<ComboboxSelected>>", lambda e: self._apply_quality())
        self.status = ttk.Label(top, text="Not connected")
        self.status.pack(side="left", padx=8)

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        side = ttk.Frame(body, padding=6, style="Side.TFrame")
        side.pack(side="right", fill="y")
        buttons = [
            ("Home", lambda: self.cmd(self._home)),
            ("App Switcher", lambda: self._sys("app_switcher")),
            ("Back", lambda: self.cmd(lambda w: w.back())),
            ("Control Center", lambda: self._sys("control_center")),
            ("Notifications", lambda: self.cmd(lambda w: w.notifications())),
            ("Spotlight", lambda: self.cmd(lambda w: w.spotlight())),
            ("Volume +", lambda: self.cmd(lambda w: w.press("volumeUp"))),
            ("Volume -", lambda: self.cmd(lambda w: w.press("volumeDown"))),
            ("Lock", lambda: self.cmd(lambda w: w.lock())),
            ("Wake / Unlock", lambda: self.cmd(lambda w: w.unlock())),
            ("Screenshot", lambda: self.cmd(self._screenshot)),
            ("Paste PC clipboard", self._paste_clipboard),
        ]
        for text, fn in buttons:
            ttk.Button(side, text=text, command=fn, width=18).pack(fill="x", pady=2)
        self.home_btn = tk.BooleanVar(value=has_home_button(self.cfg.get("product_type")))
        ttk.Checkbutton(side, text="Home-button iPhone", variable=self.home_btn).pack(anchor="w", pady=(8, 4))
        tk.Label(side, text=HELP, justify="left", bg="#1b1b1b", fg="#8a8a8a",
                 font=("Consolas", 8)).pack(anchor="w", pady=6)

        self.canvas = tk.Canvas(body, bg="#000", highlightthickness=0, cursor="hand2")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.img_item = self.canvas.create_image(0, 0, anchor="nw")
        self.placeholder = self.canvas.create_text(10, 10, anchor="nw", fill="#777", font=("Segoe UI", 11),
                                                   text="Choose USB or Wi-Fi and press Connect.")
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_down)
        self.canvas.bind("<B1-Motion>", self._on_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_up)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Button-2>", lambda e: self.cmd(self._home))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Key>", self._on_key)

        self.logbox = tk.Text(self, height=6, bg="#0d0d0d", fg="#9ad", insertbackground="#9ad",
                              relief="flat", font=("Consolas", 9))
        self.logbox.pack(side="bottom", fill="x")

    # ================================================================== logging / status
    def log_threadsafe(self, msg: str):
        self._ui_q.put(("log", msg))

    def _log(self, msg: str):
        self.logbox.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n")
        self.logbox.see("end")

    def set_status(self, msg: str):
        self._ui_q.put(("status", msg))

    # ================================================================== connection
    def connect(self):
        self.disconnect(quiet=True)
        self.btn_connect.state(["disabled"])
        wifi = self.mode.get().startswith("Wi-Fi")
        self.cfg["mode"] = "wifi" if wifi else "usb"
        preset = QUALITY.get(self.quality.get(), QUALITY["Balanced"])
        threading.Thread(target=self._connect_worker, args=(wifi, self.ip_var.get(), preset), daemon=True).start()

    def _connect_worker(self, wifi: bool, ip: str, preset: tuple):
        try:
            self.set_status("Connecting...")
            if self.args.host:  # dev/testing: direct WDA endpoint
                host, port, mport = self.args.host, self.args.port, self.args.mjpeg_port
            elif wifi:
                host, port, mport = self.dm.connect_wifi(ip)
            else:
                host, port, mport = self.dm.connect_usb()
            w = WDA(host, port, mport)
            w.new_session()
            w.configure_stream(*preset)
            w.refresh_size()
            phone_ip = w.device_ip()
            if phone_ip and not self.args.host:
                self.dm.remember_ip(phone_ip)
                self._ui_q.put(("ip", phone_ip))
            self.wda = w
            self._ui_q.put(("home_btn", has_home_button(self.dm.cfg.get("product_type"))))
            self.reader = MJPEGReader(w.mjpeg_url, self._on_frame, self._on_stream_state)
            self.reader.start()
            self.log_threadsafe(f"Connected: {host}:{port}  screen {w.size[0]:.0f}x{w.size[1]:.0f} pt"
                                + (f"  (phone Wi-Fi IP {phone_ip})" if phone_ip else ""))
        except Exception as e:
            self.log_threadsafe(f"Connect failed: {e}")
            self.set_status("Not connected")
        finally:
            self._ui_q.put(("connect_done", None))

    def disconnect(self, quiet: bool = False):
        if self.reader:
            self.reader.stop()
            self.reader = None
        self.wda = None
        if not quiet:
            self.set_status("Not connected")

    def on_close(self):
        self.disconnect(quiet=True)
        self.dm.cfg.update(self.cfg | {"quality": self.quality.get(), "ip": self.ip_var.get()
                                       or self.dm.cfg.get("ip", "")})
        try:
            from .device import save_config
            save_config(self.dm.cfg)
        except Exception:
            pass
        if self.args.keep_wda:
            self.dm.procs.clear()  # leave forwards / XCUITest running
        self.dm.shutdown()
        self.destroy()

    # ================================================================== commands (serialized)
    def cmd(self, fn):
        if not self.wda:
            self.set_status("Not connected")
            return
        self._cmd_q.put(fn)

    def _cmd_worker(self):
        while True:
            fn = self._cmd_q.get()
            w = self.wda
            if w is None:
                continue
            try:
                fn(w)
            except Exception as e:
                self.log_threadsafe(f"Command failed: {e}")

    def _home(self, w: WDA):
        w.home()

    def _apply_quality(self):
        name = self.quality.get()
        preset = QUALITY[name]

        def run(w: WDA):
            w.configure_stream(*preset)
            self.log_threadsafe(f"Stream quality: {name}")
        self.cmd(run)

    def _sys(self, gesture: str):
        """App switcher / Control Center differ on Home-button phones; read the toggle on the UI thread."""
        hb = self.home_btn.get()
        self.cmd(lambda w: getattr(w, gesture)(hb))

    def _screenshot(self, w: WDA):
        png = w.screenshot_png()
        d = Path.home() / "Pictures" / "iPhone"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"iphone_{datetime.now():%Y%m%d_%H%M%S}.png"
        p.write_bytes(png)
        self.log_threadsafe(f"Saved {p}")

    def _paste_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if text:
            self.cmd(lambda w: w.type_text(text))

    # ================================================================== frames
    def _on_stream_state(self, s: str):
        self._stream_state = s

    def _on_frame(self, jpeg: bytes):
        """Runs on the reader thread: decode + scale to the canvas, leave for the UI tick."""
        try:
            im = Image.open(io.BytesIO(jpeg))
            fw, fh = im.size
            cw, ch = self._canvas_wh
            s = min(cw / fw, ch / fh)
            dw, dh = max(1, int(fw * s)), max(1, int(fh * s))
            im.draft("RGB", (dw, dh))
            im = im.convert("RGB")
            if im.size != (dw, dh):
                im = im.resize((dw, dh), Image.LANCZOS)  # sharper small text than bilinear
            self._pending = (im, ((cw - dw) // 2, (ch - dh) // 2, dw, dh, fw, fh))
        except Exception:
            pass

    def _tick(self):
        try:
            while True:
                kind, val = self._ui_q.get_nowait()
                if kind == "log":
                    self._log(val)
                elif kind == "status":
                    self.status.configure(text=val)
                elif kind == "ip":
                    self.ip_var.set(val)
                elif kind == "home_btn":
                    self.home_btn.set(val)
                elif kind == "connect_done":
                    self.btn_connect.state(["!disabled"])
        except queue.Empty:
            pass

        p, self._pending = self._pending, None
        if p is not None:
            im, disp = p
            self.photo = ImageTk.PhotoImage(im)
            self.canvas.itemconfigure(self.img_item, image=self.photo)
            self.canvas.coords(self.img_item, disp[0], disp[1])
            self.canvas.itemconfigure(self.placeholder, state="hidden")
            self.disp = disp
            self._frames += 1
            self._check_orientation(disp[4], disp[5])

        now = time.time()
        if now - self._fps_t >= 1.0:
            self._fps = self._frames / (now - self._fps_t)
            self._frames, self._fps_t = 0, now
            if self.wda:
                self.status.configure(text=f"{self._stream_state}  {self._fps:4.1f} fps")
        self.after(15, self._tick)

    def _check_orientation(self, fw: int, fh: int):
        w = self.wda
        if not w or time.time() - self._last_size_check < 1.0:
            return
        if (fw > fh) != (w.size[0] > w.size[1]):
            self._last_size_check = time.time()
            self.cmd(lambda w: w.refresh_size())

    def _on_resize(self, e):
        self._canvas_wh = (max(e.width, 50), max(e.height, 50))

    # ================================================================== input mapping
    def _to_pt(self, cx: float, cy: float) -> tuple[float, float]:
        if not self.disp or not self.wda:
            return (0.0, 0.0)
        ox, oy, dw, dh, _, _ = self.disp
        W, H = self.wda.size
        x = (cx - ox) / dw * W
        y = (cy - oy) / dh * H
        return (min(max(x, 0), W - 1), min(max(y, 0), H - 1))

    def _ms(self) -> float:
        return (time.perf_counter() - self._g_t0) * 1000

    def _on_down(self, e):
        self.canvas.focus_set()
        if not self.wda or not self.disp:
            return
        self._g_t0 = time.perf_counter()
        x, y = self._to_pt(e.x, e.y)
        self._gesture = [(0.0, x, y)]
        self._touch_dot = self.canvas.create_oval(e.x - 9, e.y - 9, e.x + 9, e.y + 9,
                                                  outline="#0a84ff", width=3)

    def _on_move(self, e):
        if self._gesture is None:
            return
        t = self._ms()
        x, y = self._to_pt(e.x, e.y)
        lt, lx, ly = self._gesture[-1]
        if t - lt >= 12 and abs(x - lx) + abs(y - ly) >= 1:
            self._gesture.append((t, x, y))
        if self._touch_dot:
            self.canvas.coords(self._touch_dot, e.x - 9, e.y - 9, e.x + 9, e.y + 9)

    def _on_up(self, e):
        g, self._gesture = self._gesture, None
        if self._touch_dot:
            self.canvas.delete(self._touch_dot)
            self._touch_dot = None
        if g is None:
            return
        t = self._ms()
        x, y = self._to_pt(e.x, e.y)
        g.append((t, x, y))
        x0, y0 = g[0][1], g[0][2]
        spread = max(abs(px - x0) + abs(py - y0) for _, px, py in g)
        if spread < TAP_SLOP_PT and t < TAP_MAX_MS:
            self.cmd(lambda w: w.tap(x0, y0))
        elif spread < TAP_SLOP_PT:
            self.cmd(lambda w: w.path([g[0]], hold_end_ms=int(t)))
        else:
            pts = _downsample(g, 80)
            self.cmd(lambda w: w.path(pts))

    def _on_right(self, e):
        self.canvas.focus_set()
        if self.wda and self.disp:
            _, y = self._to_pt(e.x, e.y)
            self.cmd(lambda w: w.back(y))

    def _on_wheel(self, e):
        if not self.wda or not self.disp:
            return
        self._wheel_acc += e.delta
        self._wheel_pos = self._to_pt(e.x, e.y)
        self._wheel_shift = bool(e.state & 0x1)
        if self._wheel_job:
            self.after_cancel(self._wheel_job)
        self._wheel_job = self.after(90, self._flush_wheel)

    def _flush_wheel(self):
        self._wheel_job = None
        acc, self._wheel_acc = self._wheel_acc, 0
        w = self.wda
        if not w or not acc:
            return
        W, H = w.size
        x, y = self._wheel_pos
        notches = acc / 120.0
        if self._wheel_shift:
            d = max(-0.6 * W, min(0.6 * W, notches * 0.15 * W))
            x0 = min(max(x, 0.05 * W - min(d, 0)), 0.95 * W - max(d, 0))
            self.cmd(lambda w: w.swipe((x0, y), (x0 + d, y), ms=150, hold_end_ms=60))
        else:
            # Wheel up (positive) => content moves down => finger moves down.
            d = max(-0.6 * H, min(0.6 * H, notches * 0.12 * H))
            y0 = min(max(y, 0.08 * H - min(d, 0)), 0.92 * H - max(d, 0))
            self.cmd(lambda w: w.swipe((x, y0), (x, y0 + d), ms=150, hold_end_ms=60))

    def _on_key(self, e):
        if not self.wda:
            return
        ctrl = bool(e.state & 0x4)
        ks = e.keysym
        special = {
            "Home": lambda w: w.home(), "F1": lambda w: w.home(),
            "F2": lambda: self._sys("app_switcher"),
            "F3": lambda: self._sys("control_center"),
            "F4": lambda w: w.notifications(), "F5": lambda w: w.spotlight(),
            "Prior": lambda w: w.press("volumeUp"), "Next": lambda w: w.press("volumeDown"),
            "F12": self._screenshot,
        }
        if ctrl:
            if ks.lower() == "v":
                self._paste_clipboard()
            return "break"
        if ks in special:
            fn = special[ks]
            fn() if ks in ("F2", "F3") else self.cmd(fn)
            return "break"
        ch = {"BackSpace": "\b", "Return": "\n", "KP_Enter": "\n", "Tab": "\t"}.get(ks, e.char)
        if ch and (ch in "\b\n\t" or ch.isprintable()):
            self._typed += ch
            if self._type_job:
                self.after_cancel(self._type_job)
            self._type_job = self.after(70, self._flush_typed)
        return "break"

    def _flush_typed(self):
        self._type_job = None
        text, self._typed = self._typed, ""
        if text:
            self.cmd(lambda w: w.type_text(text))


def _downsample(g, n):
    if len(g) <= n:
        return g
    step = (len(g) - 1) / (n - 1)
    return [g[round(i * step)] for i in range(n)]


def main():
    ap = argparse.ArgumentParser(description="Mirror and control an iPhone from Windows.")
    ap.add_argument("--ip", help="connect over Wi-Fi to a phone already running WDA")
    ap.add_argument("--autoconnect", action="store_true", help="connect on launch using the saved mode")
    ap.add_argument("--keep-wda", action="store_true", help="leave WDA running on the phone after closing")
    ap.add_argument("--notice", help=argparse.SUPPRESS)  # message from the launcher, shown in the log
    ap.add_argument("--host", help=argparse.SUPPRESS)  # testing: raw WDA endpoint (e.g. mock server)
    ap.add_argument("--port", type=int, default=8100, help=argparse.SUPPRESS)
    ap.add_argument("--mjpeg-port", type=int, default=9100, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App(args).mainloop()


if __name__ == "__main__":
    main()
