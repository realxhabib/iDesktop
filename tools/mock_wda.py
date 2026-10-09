"""Fake WebDriverAgent for testing the controller without a phone.

Serves the WDA HTTP API on :8100 and an MJPEG stream on :9100. Renders a fake home screen that
shows the last touch path and typed text, and logs every command (stdout + optional JSONL file).
"""
import argparse
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

W, H = 390, 844          # points
SCALE = 2                # rendered pixels per point (before mjpegScalingFactor)
state = {"touches": [], "text": "", "locked": False, "settings": {}, "events": []}
lock = threading.Lock()
LOG = None


def log(ev):
    ev["t"] = round(time.time(), 3)
    with lock:
        state["events"].append(ev)
    print(json.dumps(ev), flush=True)
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps(ev) + "\n")


def render():
    im = Image.new("RGB", (W * SCALE, H * SCALE), (20, 30, 60))
    d = ImageDraw.Draw(im)
    for r in range(6):
        for c in range(4):
            x, y = (30 + c * 90) * SCALE, (80 + r * 100) * SCALE
            d.rounded_rectangle([x, y, x + 60 * SCALE, y + 60 * SCALE], 14 * SCALE,
                                fill=(60 + r * 25, 120, 200 - c * 30))
    d.text((20 * SCALE, 20 * SCALE), time.strftime("%H:%M:%S"), fill="white")
    with lock:
        touches = list(state["touches"])
        text = state["text"]
        locked = state["locked"]
    for path in touches[-5:]:
        pts = [(x * SCALE, y * SCALE) for x, y in path]
        if len(pts) > 1:
            d.line(pts, fill=(255, 200, 0), width=6)
        x, y = pts[-1]
        d.ellipse([x - 14, y - 14, x + 14, y + 14], outline=(255, 80, 80), width=5)
    d.rectangle([0, (H - 60) * SCALE, W * SCALE, H * SCALE], fill=(0, 0, 0))
    d.text((10 * SCALE, (H - 50) * SCALE), "typed: " + text[-40:], fill="white")
    if locked:
        d.rectangle([0, 0, W * SCALE, H * SCALE], fill=(0, 0, 0))
        d.text((150 * SCALE, 400 * SCALE), "LOCKED", fill="white")
    s = state["settings"].get("mjpegScalingFactor", 100) / 100
    if s != 1:
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=state["settings"].get("mjpegServerScreenshotQuality", 60))
    return b.getvalue()


class API(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, value, sid="mock-sid", code=200):
        body = json.dumps({"value": value, "sessionId": sid}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = self.path
        if p == "/status":
            return self._send({"ready": True, "ios": {"ip": "127.0.0.1"}, "state": "success"})
        if p.endswith("/window/size"):
            return self._send({"width": W, "height": H})
        if p == "/wda/locked":
            return self._send(state["locked"])
        if p == "/screenshot":
            import base64
            return self._send(base64.b64encode(render()).decode())
        self._send({"error": "unknown command", "message": p}, code=404)

    def do_POST(self):
        p, b = self.path, self._body()
        if p == "/session":
            log({"cmd": "session"})
            return self._send({"sessionId": "mock-sid", "capabilities": {}})
        if p.endswith("/appium/settings"):
            state["settings"].update(b.get("settings", {}))
            log({"cmd": "settings", **b.get("settings", {})})
            return self._send(state["settings"])
        if p.endswith("/actions"):
            steps = b["actions"][0]["actions"]
            path, cur, down = [], None, False
            for s in steps:
                if s["type"] == "pointerMove":
                    cur = (s["x"], s["y"])
                    if down:
                        path.append(cur)
                elif s["type"] == "pointerDown":
                    down = True
                    path.append(cur)
                elif s["type"] == "pointerUp":
                    down = False
            with lock:
                state["touches"].append(path)
            dur = sum(s.get("duration", 0) for s in steps)
            log({"cmd": "actions", "n_steps": len(steps), "start": path[0], "end": path[-1], "ms": dur})
            return self._send(None)
        if p.endswith("/wda/keys"):
            txt = "".join(b["value"])
            with lock:
                for ch in txt:
                    state["text"] = state["text"][:-1] if ch == "\b" else state["text"] + ch
            log({"cmd": "keys", "text": txt})
            return self._send(None)
        if p == "/wda/homescreen":
            log({"cmd": "home"}); return self._send(None)
        if p.endswith("/wda/pressButton"):
            log({"cmd": "button", "name": b.get("name")}); return self._send(None)
        if p in ("/wda/lock", "/wda/unlock"):
            state["locked"] = p == "/wda/lock"
            log({"cmd": p.rsplit("/", 1)[1]}); return self._send(None)
        log({"cmd": "unknown", "path": p})
        self._send({"error": "unknown command", "message": p}, code=404)


class MJPEG(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=--BoundaryString")
        self.end_headers()
        try:
            while True:
                fps = state["settings"].get("mjpegServerFramerate", 10)
                jpg = render()
                self.wfile.write(b"--BoundaryString\r\nContent-type: image/jpg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n\r\n")
                time.sleep(1 / max(fps, 1))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--mjpeg-port", type=int, default=9100)
    ap.add_argument("--log")
    a = ap.parse_args()
    LOG = a.log
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", a.mjpeg_port), MJPEG).serve_forever,
                     daemon=True).start()
    print(f"mock WDA on :{a.port}, MJPEG on :{a.mjpeg_port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), API).serve_forever()
