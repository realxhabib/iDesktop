"""Floating phone: clip the viewer's Edge window to the phone, its side buttons and controls.

The window is maximized (maximized windows get no DWM shadow or border) and given a window
region of the shapes the page reports; outside them it is invisible and click-through. Edge's
Mica backdrop ignores regions, so it's switched off while clipped. Don't restyle the window
(caption bits, colour keys, NC rendering): that flips Chromium to its custom frame, which wipes
the region on every move. The page reports shapes in CSS pixels; regions are physical pixels
relative to the window's top-left corner.
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

TITLE = "iDesktop"

if os.name == "nt":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:  # per-monitor DPI aware: all coordinates are physical pixels
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
    gdi32.CreateRectRgn.restype = wintypes.HANDLE
    gdi32.CombineRgn.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_int]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]

SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x1, 0x4, 0x10
RGN_OR = 2
WM_CLOSE, SW_MINIMIZE, SW_RESTORE, SW_MAXIMIZE = 0x0010, 6, 9, 3

_lock = threading.Lock()
_hwnd = None
_last_spec = None      # the shape to keep (None: unclipped)
_watch = None


def _set_backdrop(hwnd, kind: int) -> None:
    """Windows 11 paints the window's Mica backdrop (DWMWA_SYSTEMBACKDROP_TYPE, 38) over the whole
    window rect regardless of the region, which shows as a grey box behind the phone while the
    window is active. 1 = none, 2 = Mica (Edge's default)."""
    try:
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(kind)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 38, ctypes.byref(v), 4)
        # ...and its 1px window border (DWMWA_BORDER_COLOR, 34): none while clipped, default after.
        c = ctypes.c_uint(0xFFFFFFFE if kind == 1 else 0xFFFFFFFF)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 34, ctypes.byref(c), 4)
    except Exception:
        pass


def _rect(hwnd) -> wintypes.RECT:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def find_window():
    """The viewer's top-level Edge/Chrome window (title set by the page)."""
    global _hwnd
    if _hwnd and user32.IsWindow(_hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(_hwnd, buf, 256)
        if buf.value.startswith(TITLE):
            return _hwnd
    _hwnd = None
    h = None
    while True:
        h = user32.FindWindowExW(None, h, "Chrome_WidgetWin_1", None)
        if not h:
            return None
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        if buf.value == TITLE and user32.IsWindowVisible(h):
            _hwnd = h
            return h


def _content_rect(hwnd) -> wintypes.RECT:
    """Screen rect of the web content: the render widget child when present, else the client area."""
    child = user32.FindWindowExW(hwnd, None, "Chrome_RenderWidgetHostHWND", None)
    if not child:
        # Newer Chromium nests it one level down.
        inter = user32.FindWindowExW(hwnd, None, None, None)
        while inter and not child:
            child = user32.FindWindowExW(inter, None, "Chrome_RenderWidgetHostHWND", None)
            inter = user32.FindWindowExW(hwnd, inter, None, None)
    if child:
        return _rect(child)
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return wintypes.RECT(pt.x, pt.y, pt.x + r.right, pt.y + r.bottom)


def _has_region(hwnd) -> bool:
    rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
    try:
        return user32.GetWindowRgn(hwnd, rgn) not in (0, 1)  # ERROR / NULLREGION
    finally:
        gdi32.DeleteObject(rgn)


def _watchdog() -> None:
    """Chromium drops the window region on some state changes (minimize/restore, DPI or theme
    changes); put the shape back whenever that happens."""
    import time
    while True:
        time.sleep(0.4)
        try:
            spec = _last_spec
            hwnd = find_window() if spec else None
            if hwnd and not user32.IsIconic(hwnd) and (not _has_region(hwnd) or
                                                       (spec.get("maximize") and not user32.IsZoomed(hwnd))):
                apply_layout(spec)
        except Exception:
            pass


def apply_layout(spec: dict) -> dict:
    global _last_spec, _watch
    _last_spec = None if spec.get("off") else spec
    if _watch is None and _last_spec:
        _watch = threading.Thread(target=_watchdog, daemon=True)
        _watch.start()
    return _apply(spec)


def _apply(spec: dict) -> dict:
    """spec (CSS px): {dpr, shapes: [{x, y, w, h, r}], size: [w, h]} -> resize + clip.
    {"off": true} removes the clip (normal window, or fullscreen)."""
    with _lock:
        hwnd = find_window()
        if not hwnd:
            raise RuntimeError("viewer window not found")
        if spec.get("off"):
            _set_backdrop(hwnd, 2)
            user32.SetWindowRgn(hwnd, None, True)
            if user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            if spec.get("size"):
                d = float(spec.get("dpr") or 1)
                w, h = (int(v * d) for v in spec["size"])
                user32.SetWindowPos(hwnd, None, 0, 0, w, h, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
            return {"ok": True}
        d = float(spec.get("dpr") or 1)
        win, content = _rect(hwnd), _content_rect(hwnd)
        ox, oy = content.left - win.left, content.top - win.top
        cw, ch = content.right - content.left, content.bottom - content.top
        ww, wh = win.right - win.left, win.bottom - win.top
        if spec.get("maximize"):
            # Floating phone: the window covers the whole work area (maximized windows get no
            # DWM shadow or border) and the region cuts it down to the phone; the page places
            # the phone anywhere inside, so "moving" it never moves the window.
            if not user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, SW_MAXIMIZE)
                win, content = _rect(hwnd), _content_rect(hwnd)
                ox, oy = content.left - win.left, content.top - win.top
            tw, th = cw, ch = 0, 0
        else:
            tw, th = (int(round(v * d)) for v in spec["size"])
        if (tw, th) != (cw, ch):
            user32.SetWindowPos(hwnd, None, 0, 0, ww + tw - cw, wh + th - ch,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
            win, content = _rect(hwnd), _content_rect(hwnd)
            ox, oy = content.left - win.left, content.top - win.top
        rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
        for s in spec.get("shapes", []):
            x0, y0 = ox + int(round(s["x"] * d)), oy + int(round(s["y"] * d))
            x1, y1 = x0 + int(round(s["w"] * d)), y0 + int(round(s["h"] * d))
            r = max(0, int(round(s.get("r", 0) * d * 2)))
            part = gdi32.CreateRoundRectRgn(x0, y0, x1 + 1, y1 + 1, r, r)
            gdi32.CombineRgn(rgn, rgn, part, RGN_OR)
            gdi32.DeleteObject(part)
        user32.SetWindowRgn(hwnd, rgn, True)  # the system owns rgn from here
        _set_backdrop(hwnd, 1)
        return {"ok": True, "content": [content.right - content.left, content.bottom - content.top]}


def move_by(dx: int, dy: int) -> None:
    with _lock:
        hwnd = find_window()
        if not hwnd:
            return
        r = _rect(hwnd)
        user32.SetWindowPos(hwnd, None, r.left + int(dx), r.top + int(dy), 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def command(cmd: str) -> None:
    hwnd = find_window()
    if not hwnd:
        return
    if cmd == "min":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif cmd == "close":
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
