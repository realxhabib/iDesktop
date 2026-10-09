"""Tiny DevTools helper for debugging the viewer: python tools/cdp.py "<js expression>" [port]"""
import json
import sys

import requests
import websocket

port = sys.argv[2] if len(sys.argv) > 2 else "9333"
pg = [p for p in requests.get(f"http://127.0.0.1:{port}/json").json() if p["url"].startswith("http://127.0.0.1:8080")][0]
ws = websocket.create_connection(pg["webSocketDebuggerUrl"], suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": sys.argv[1], "returnByValue": True, "awaitPromise": True}}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 1:
        r = m["result"]
        print(r.get("result", {}).get("value", r))
        break
