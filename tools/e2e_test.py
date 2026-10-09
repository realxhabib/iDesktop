"""End-to-end test of the viewer against tools/mock_wda.py (no phone needed).

Starts the mock, launches the real Tk app pointed at it, injects mouse/keyboard events into the
canvas and asserts that the mock received correctly mapped WDA commands.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API, MJ = 18100, 19100


def main():
    logf = Path(tempfile.gettempdir()) / f"mockwda_{os.getpid()}.jsonl"
    mock = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mock_wda.py"), "--port", str(API),
                             "--mjpeg-port", str(MJ), "--log", str(logf)], stdout=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        run(logf)
    finally:
        mock.terminate()


def events(logf):
    if not logf.exists():
        return []
    return [json.loads(l) for l in logf.read_text().splitlines() if l.strip()]


def run(logf):
    from idesktop import controller

    args = argparse.Namespace(ip=None, autoconnect=True, keep_wda=False, host="127.0.0.1", port=API,
                              mjpeg_port=MJ)
    app = controller.App(args)
    app.geometry("760x980+40+20")
    results = {}

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.update()
            time.sleep(0.01)

    pump(4)
    assert app.wda is not None, "did not connect"
    assert app.disp is not None, "no frames displayed"
    ox, oy, dw, dh, fw, fh = app.disp
    results["display"] = app.disp
    W, H = app.wda.size

    def canvas_xy(px, py):  # points -> canvas pixels
        return int(ox + px / W * dw), int(oy + py / H * dh)

    c = app.canvas
    # 1) tap at (100, 200) pt
    x, y = canvas_xy(100, 200)
    c.event_generate("<ButtonPress-1>", x=x, y=y); pump(0.05)
    c.event_generate("<ButtonRelease-1>", x=x, y=y); pump(0.8)

    # 2) swipe up from (195, 700) to (195, 300)
    x0, y0 = canvas_xy(195, 700); x1, y1 = canvas_xy(195, 300)
    c.event_generate("<ButtonPress-1>", x=x0, y=y0)
    for i in range(1, 21):
        pump(0.02)
        c.event_generate("<B1-Motion>", x=x0, y=int(y0 + (y1 - y0) * i / 20), state=0x100)
    c.event_generate("<ButtonRelease-1>", x=x1, y=y1); pump(1.0)

    # 3) long press at (300, 400)
    x, y = canvas_xy(300, 400)
    c.event_generate("<ButtonPress-1>", x=x, y=y); pump(0.7)
    c.event_generate("<ButtonRelease-1>", x=x, y=y); pump(1.0)

    # 4) typing
    c.focus_force(); pump(0.1)
    for ch in "hi":
        c.event_generate("<Key>", keysym=ch); pump(0.01)
    c.event_generate("<Key>", keysym="BackSpace"); pump(0.01)
    c.event_generate("<Key>", keysym="o"); pump(0.6)

    # 5) wheel scroll down 2 notches
    x, y = canvas_xy(195, 420)
    c.event_generate("<MouseWheel>", x=x, y=y, delta=-240); pump(0.8)

    # 6) buttons / hotkeys
    c.event_generate("<Key>", keysym="Home"); pump(0.3)
    c.event_generate("<Key>", keysym="Prior"); pump(0.3)
    c.event_generate("<Button-3>", x=x, y=y); pump(0.6)

    fps_seen = app._fps
    pump(1.5)
    fps_seen = max(fps_seen, app._fps)
    app.on_close()

    ev = events(logf)
    acts = [e for e in ev if e["cmd"] == "actions"]
    keys = "".join(e["text"] for e in ev if e["cmd"] == "keys")
    print(json.dumps({"fps": round(fps_seen, 1), "display": results["display"]}, indent=None))
    for e in ev:
        print("  ", e)

    def near(p, q, tol=6):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    checks = {
        "session+settings": any(e["cmd"] == "settings" for e in ev),
        "tap mapped": len(acts) > 0 and near(acts[0]["start"], (100, 200)) and acts[0]["n_steps"] == 4,
        "swipe mapped": len(acts) > 1 and near(acts[1]["start"], (195, 700)) and near(acts[1]["end"], (195, 300)),
        "long press": len(acts) > 2 and near(acts[2]["start"], (300, 400)) and acts[2]["ms"] >= 500,
        "typing (with backspace)": keys == "hi\bo",
        "wheel -> upward swipe": len(acts) > 3 and acts[3]["end"][1] < acts[3]["start"][1],
        "home key": any(e["cmd"] == "home" for e in ev),
        "volume key": any(e["cmd"] == "button" and e["name"] == "volumeUp" for e in ev),
        "right-click back": len(acts) > 4 and acts[4]["start"][0] <= 2 and acts[4]["end"][0] > 200,
        "stream fps > 10": fps_seen > 10,
    }
    print()
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
