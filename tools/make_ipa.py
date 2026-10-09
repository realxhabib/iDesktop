"""Wrap an unsigned .app into an .ipa (Payload/<App>.app) for Sideloadly. Usage: make_ipa.py <app> <out.ipa>"""
import os
import sys
import zipfile

src, out = sys.argv[1], sys.argv[2]
name = os.path.basename(os.path.normpath(src))
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        if ".dSYM" in root:
            continue
        for f in files:
            p = os.path.join(root, f)
            z.write(p, f"Payload/{name}/" + os.path.relpath(p, src).replace(os.sep, "/"))
print(f"wrote {out} ({os.path.getsize(out)} bytes)")
