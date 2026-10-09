"""Syntax-check the JavaScript that viewer_layer.py injects into the viewer page (needs Node.js).
Usage: python tools/check_viewer_js.py"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from idesktop import viewer_layer  # noqa: E402

js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", viewer_layer.HEAD_INJECT + viewer_layer.BODY_INJECT, re.S))
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
    f.write(js)
r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
Path(f.name).unlink()
if r.returncode:
    print(r.stderr)
    sys.exit(1)
print(f"viewer JS OK ({len(js)} chars)")
