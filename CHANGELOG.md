# Changelog

## Unreleased

- App Switcher works again: it now goes through Automation (iOS ignores edge swipes from the HID touch input).
- Turning PC sound on drops the phone's volume to its lowest step; volume uses the phone's own keys, no Automation needed.
- Passcode keypad waits until the phone has stayed locked for a moment, and doesn't pop back up while a code is being checked or just after unlocking.
- Automation's helper threads share one WebDriverAgent session instead of knocking each other's out.

## 0.1.0 (first public release)

- HD mirror (iOS 27+): the iPhone's own HEVC stream at up to 60 fps, drawn sharp at window resolution.
- Floating phone window, with a titanium rim and working side buttons; floating glass controls; Big Screen and Desktop Mode.
- Touch, drag, scroll and pinch; PC keyboard with Alt as ⌘; on-screen keyboard toggle (Ctrl+K).
- Phone audio on the PC (FFmpeg AAC-ELD decoder), with a "Sound on PC only" option.
- Two-way clipboard sync.
- Wireless: no cable and no admin rights after one wired start. It finds the phone on the network without Bonjour and reconnects by itself.
- Auto-rotate for landscape apps.
- Lite mode that keeps the mirror live and controllable during calls (WebDriverAgent, an optional extra).
- One-line installer, uninstaller, and a first-run guide (Trust, Developer Mode, developer disk image).
