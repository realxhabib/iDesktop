# Contributing to iDesktop

Thanks for helping! Bug reports from different iPhones, iOS versions and PCs are the most useful contribution right now. iDesktop has only been tested on a few setups.

## Reporting a bug

Open an [issue](../../issues/new/choose) using the bug template, and include:

- **Setup:** iPhone model and iOS version, Windows version, and whether you're on USB or Wi-Fi.
- **What happened:** what you did, what you expected, and what happened instead.
- **Logs** from `%APPDATA%\iDesktop\logs`. `native.log` and `launcher.log` are the useful ones.

Logs can contain your phone's name, UDID and local IP address. Redact them if you prefer.

## Development setup

```powershell
git clone https://github.com/realxhabib/iDesktop.git
cd iDesktop
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m idesktop          # run from source
```

`install.ps1` run from a clone installs that clone, which is handy for testing the installer itself.

### Layout

| Path | What |
|---|---|
| `idesktop/app.py` | Launcher: first-run setup, USB/Wi-Fi selection, reconnects, fallback to basic mode |
| `idesktop/native.py` | The HD stream process (pymobiledevice3 `serve-web` plus patches), and the helper process (pinch, orientation, window shaping) |
| `idesktop/viewer_layer.py` | Everything injected into the viewer page: floating phone UI, controls, touch handling, rendering fixes |
| `idesktop/winshape.py` | Clips the Edge window to the phone's shape |
| `idesktop/device.py` | pymobiledevice3 helpers, config, first-run checks, Wi-Fi discovery |
| `idesktop/controller.py`, `wda.py` | Basic mode: Tk viewer driven by WebDriverAgent |
| `tools/` | Tests and dev helpers (`e2e_test.py`, `mock_wda.py`, `check_viewer_js.py`, `cdp.py`, `make_icon.py`, `make_ipa.py`) |

### Tips

- **Hot reload:** `viewer_layer.py` is re-read on every page load, and the open viewer reloads itself when it changes, without dropping the stream. That matters because iOS refuses to start a new stream during calls.
- **DevTools:** set `IDESKTOP_DEVTOOLS=9333` before starting, then use `python tools/cdp.py "<js expression>"`.
- **Page errors:** JavaScript errors in the viewer are forwarded to `helper.log`.
- **Audio packets:** `IDESKTOP_AUDIO_DUMP=<file>` saves raw AAC-ELD packets for decoder debugging.

### Before sending a PR

```powershell
.venv\Scripts\python -m compileall -q idesktop tools
.venv\Scripts\python tools\check_viewer_js.py     # needs Node.js
.venv\Scripts\python tools\e2e_test.py
```

CI runs the same checks on Windows. Keep PRs focused, and match the existing style: small functions, comments that explain *why*.

## License

By contributing you agree that your contributions are licensed under the GPL-3.0-or-later, the same as the project.
