"""Exercise code paths that only run with a phone / viewer window, as far as possible without
either: catches missing names and broken wiring that compiling alone doesn't (no phone needed).
Usage: python tools/smoke_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

failures = []


def check(name, fn):
    try:
        fn()
        print("PASS", name)
    except Exception as e:  # noqa: BLE001 - report everything
        failures.append(name)
        print("FAIL", name, f"{type(e).__name__}: {e}")


def winshape_without_window():
    from idesktop import winshape
    winshape._hwnd = None
    winshape.TITLE = "iDesktop smoke test (no such window)"
    assert winshape.find_window() is None
    for spec in ({"dpr": 1, "maximize": True, "shapes": [{"x": 0, "y": 0, "w": 10, "h": 10, "r": 2}]},
                 {"off": True}):
        try:
            winshape.apply_layout(spec)
        except RuntimeError as e:
            assert "not found" in str(e)
        else:
            raise AssertionError("expected 'viewer window not found'")
    winshape.move_by(1, 1)      # no window: quietly nothing
    winshape.command("min")


def native_patches_apply():
    from pymobiledevice3.remote.core_device import screen_stream as ss

    from idesktop import native
    native.patch_screen_stream(18091, "com.example.wda", 18100)
    native.patch_wifi_fallback("192.0.2.1")
    assert "keyboard" in ss._NAMED_BUTTONS
    html = native.build_html(ss.VIEWER_HTML if isinstance(ss.VIEWER_HTML, bytes) else ss.VIEWER_HTML.encode(), 18091)
    assert b"<title>iDesktop</title>" in html and b"127.0.0.1:18091" in html


def audio_decoder_opens():
    from idesktop.native import FFmpegAACELDDecoder
    d = FFmpegAACELDDecoder()
    assert d.decode(b"\x00\x00") == FFmpegAACELDDecoder.SILENCE


def helpers_import():
    from idesktop import app, controller, device, wda
    assert device.CONFIG_DIR.name == "iDesktop"
    assert callable(controller.main) and hasattr(wda, "WDA")
    native = __import__("idesktop.native", fromlist=["x"])
    assert (app.RECONNECT_EXIT, app.DISCONNECT_EXIT, app.RECONNECT_FLAG) ==         (native.RECONNECT_EXIT, native.DISCONNECT_EXIT, native.RECONNECT_FLAG)


check("winshape without a window", winshape_without_window)
check("native patches apply", native_patches_apply)
check("audio decoder opens", audio_decoder_opens)
check("modules import", helpers_import)
sys.exit(1 if failures else 0)
