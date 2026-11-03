"""Tiny DevTools helper for debugging the viewer: python tools/cdp.py "<js expression>" [port]"""
import json
import sys
from urllib.parse import urlsplit

import requests
import websocket

def find_viewer(pages):
    for page in pages:
        url = urlsplit(page.get("url", ""))
        if (page.get("type") == "page" and page.get("title", "").startswith("iDesktop")
                and url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")
                and url.path in ("", "/")):
            return page
    raise RuntimeError("iDesktop viewer not found; open the viewer with IDESKTOP_DEVTOOLS enabled first")


def main():
    port = sys.argv[2] if len(sys.argv) > 2 else "9333"
    response = requests.get(f"http://127.0.0.1:{port}/json", timeout=5)
    response.raise_for_status()
    pg = find_viewer(response.json())
    ws = websocket.create_connection(pg["webSocketDebuggerUrl"], suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": sys.argv[1], "returnByValue": True, "awaitPromise": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                r = m["result"]
                print(r.get("result", {}).get("value", r))
                break
    finally:
        ws.close()


if __name__ == "__main__":
    main()
