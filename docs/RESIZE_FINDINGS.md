# iPhone app resizing over CoreDevice — findings

Tested 2026-10-13 · macOS 27.0.1 · Xcode 27.0 (27A266a, CoreDevice 642.16) · iPhone 16 Pro Max (iPhone17,2) on iOS 27.0.1 (24A446), Developer Mode on, connected over Wi-Fi · pymobiledevice3 11.29.0

## Summary

- **No request exists to capture.** Device Hub's Resize Mode only works for simulators. On the physical iPhone, Xcode greys it out and `devicectl` refuses before sending anything.
- **The phone has the resize code but doesn't expose it.** Xcode 27's Developer Disk Image installs a resizable-app session server in `dtappserviced`. It is only registered as a local Mach service. The one remote entry point, `com.apple.coredevice.appservice`, answers *"Action … is not implemented"* when asked for it.
- **iOS on the phone is ready.** The read-only resizability stream *is* reachable over RemoteXPC. With Settings and Safari in front, it reports them as `hasResizableForegroundApplication`.
- **A companion app on the phone can't trigger it either (tested).** The app sandbox blocks lookups of the resize service, and SpringBoard's resizing API needs the private `com.apple.springboard.appResizing` entitlement.
- **For iDesktop:** nothing to ship yet. The Python below can detect resizable apps today. The section "How to tell when Apple opens it up" lists the signals that would show Apple has opened the session protocol to physical devices.

## Why Device Hub greys it out on a real iPhone

| | Simulator (iPhone 18 Pro, iOS 27.0) | iPhone 16 Pro Max (iOS 27.0.1) |
|---|---|---|
| Advertises `com.apple.coredevice.feature.resizableappmanagement` | yes | no |
| Session server (`ResizableAppManagementServer` in `dtappserviced`) | yes (local Mach service) | yes (local Mach service only) |
| Reachable from the host | yes, through a direct local XPC connection | no |
| `com.apple.coredevice.feature.streamresizabilitystate` | yes | **yes, works over RemoteXPC** |

`dtappserviced`'s launchd plist in Xcode 27's iOS DDI (both images, `022-20231-411.dmg` and `022-20190-411.dmg`) lists the resize features under `MachServices`. They are absent from the `Features` array of the `RemoteServices` → `com.apple.coredevice.appservice` entry. That array has 15 features: install/launch/list/kill and similar. CoreDevice decides capabilities from that array, so Xcode reports *"The capability 'Resizable App Management' is not supported by this device"* (`com.apple.dt.CoreDeviceError` 1001).

## How the session works (from the simulator and binaries)

Host side (Xcode 27):

```text
devicectl device appResize start   --device <id> [--preferred-size WxH] [--corner-radius R]
devicectl device appResize set     --device <id> --preferred-size WxH [--corner-radius R]
devicectl device appResize observe --device <id>
devicectl device info appResize    --device <id>
```

These work against a simulator. `start` targets a device display whose name contains **"Resizable"** and runs until Ctrl+C; the session ends when the command exits. Example state from the simulator after a Device Hub drag:

```text
Display: Resizable (<display uuid>)
Preferred size: 1280.0x874.0
Corner radius: 18.0
Minimum possible size: 0.0x0.0
Maximum possible size: 1280.0x1280.0
```

Device side (`dtappserviced` + `CoreDeviceUtilities` in the iOS DDI):

- **Service:** `com.apple.coredevice.resizableappmanagement`, served by `ResizableAppManagementServer` and `ResizableAppManagementSessionManager`.
- **Session operations:** start a session, `getResizableSceneState`, `adjustResizableSceneDisplayGeometry`, `cancelResizableSceneSession`.
- **Input names seen in the binaries:** `targetDisplayUniqueId`, `displayUniqueId`, `preferredSize`, `cornerRadius`. These names come from the binaries and haven't been seen on the wire, because the session protocol can't be reached on a device.
- **iOS call:** SpringBoardServices `SBSAppResizingService` → `moveFrontmostAppsToDisplay:preferredSize:resizingCornerRadius:delegate:` returns an `SBSAppResizingAssertion`. Resizing afterwards is `setPreferredSize:` on that assertion. SpringBoard reports back `preferredSize`, `resolvedSize`, `minimumPossibleSize` and `maximumPossibleSize`.
- **Target display:** on a simulator the "Resizable" display comes from the simulator framebuffer. On a device, the host would have to create it. Xcode's host CoreDevice has a `com.apple.coredevice.action.prepareresizabledisplay` action for that, presumably through `com.apple.coredevice.displayservice`, the service that created the 1328×2880 "Wireless" display while Device Hub mirrored the phone.
- **Preconditions** (from the error strings): device unlocked, in portrait, an app in the foreground that has adopted resizable scenes, and no session already on that display.
- **Undo:** the device cancels a session when its owning peer disconnects or the daemon shuts down. Device Hub resizes back to the main display size before ending a session. Clients end it explicitly with `cancelResizableSceneSession`.

## What works today: resizability stream (verified on the iPhone)

- **Service:** `com.apple.coredevice.appservice`
- **Feature:** `com.apple.coredevice.feature.streamresizabilitystate`. The phone doesn't advertise it, but answers it anyway.
- **Request:** a standard CoreDevice invocation with the streaming wrapper. `CoreDevice.input` = `{"actualInput": {}, "streamProxy": {"sideChannel": <uuid>}}`.
- **Replies:** `CoreDevice.XPCMessageKey.sideChannelStatus` → `pushing` → `elements`. One element per state change; each message also carries `XPCSideChannel.uniqueIdentifier`.

| Phone state | Element |
|---|---|
| Home Screen / locked | `{'noForegroundApplication': {}}` |
| Settings in front | `{'hasResizableForegroundApplication': {'bundleIdentifier': 'com.apple.Preferences'}}` |
| Safari in front | `{'hasResizableForegroundApplication': {'bundleIdentifier': 'com.apple.mobilesafari'}}` |

The binaries also define `hasNonResizableForegroundApplication`, `resizing` and `resizingUnavailable`. None of these was seen in this test.

No root needed. This uses pymobiledevice3's in-process userspace tunnel via usbmuxd's existing pairing, and doesn't disturb Xcode's own connection.

```python
# requires: pymobiledevice3 >= 11.29
import asyncio, uuid
from pymobiledevice3.remote.core_device.core_device_service import CORE_DEVICE_VERSION
from pymobiledevice3.remote.remote_service import RemoteService
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.remote.xpc_message import XpcInt64Type
from pymobiledevice3.usbmux import list_devices

APPSERVICE = "com.apple.coredevice.appservice"


def core_device_request(feature: str, input_: dict) -> dict:
    return {
        "CoreDevice.CoreDeviceDDIProtocolVersion": XpcInt64Type(2),
        "CoreDevice.coreDeviceVersion": CORE_DEVICE_VERSION,
        "CoreDevice.deviceIdentifier": str(uuid.uuid4()),
        "CoreDevice.featureIdentifier": feature,
        "CoreDevice.action": {},
        "CoreDevice.input": input_,
        "CoreDevice.invocationIdentifier": str(uuid.uuid4()),
    }


async def watch_resizability(seconds: float = 30) -> None:
    serial = (await list_devices())[0].serial
    async with UserspaceRsdTunnel(serial=serial, autopair=False) as rsd:
        conn = RemoteService(rsd, APPSERVICE)  # raw connection: skips require_feature()
        await conn.connect()
        try:
            await conn.service.send_request(core_device_request(
                "com.apple.coredevice.feature.streamresizabilitystate",
                {"actualInput": {}, "streamProxy": {"sideChannel": uuid.uuid4()}},
            ), wanting_reply=True)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while (left := deadline - loop.time()) > 0:
                try:
                    msg = await asyncio.wait_for(conn.service.receive_response(), left)
                except asyncio.TimeoutError:
                    break
                status = msg["CoreDevice.XPCMessageKey.sideChannelStatus"]
                for element in status.get("pushing", {}).get("elements", []):
                    print(element)  # e.g. {'hasResizableForegroundApplication': {'bundleIdentifier': ...}}
        finally:
            await conn.close()


asyncio.run(watch_resizability())
```

## How to tell when Apple opens it up

Any one of these means the session protocol has become reachable on devices. When it does, capture Device Hub with the sniffer as originally planned (`sudo tcpdump -i <utun> -w resize.pcap tcp`, then `remotexpc_sniffer.py offline resize.pcap`).

1. The `appservice` Features list includes `com.apple.coredevice.feature.resizableappmanagement`. Check with `devicectl device info details` capabilities, or `rsd.peer_info["Services"]["com.apple.coredevice.appservice"]["Properties"]["Features"]`.
2. The phone's service list gains `com.apple.coredevice.resizableappmanagement`. Check with `/usr/libexec/remotectl show <coredevice-N>`, getting the name from `remotectl list`.
3. `xcrun devicectl device info appResize --device <phone>` stops failing with error 1001.
4. Sending `com.apple.coredevice.feature.resizableappmanagement` to `appservice` with an empty input no longer returns `CoreDevice.ActionError` code 2, *"Action 'com.apple.coredevice.feature.resizableappmanagement' is not implemented."*

## Triggering it from the phone itself

A companion app on the phone, driven by the PC, can't do it on stock iOS:

- **`dtappserviced` needs a private entitlement to resize.** It holds `com.apple.springboard.appResizing`, alongside `com.apple.frontboard.launchapplications`, `com.apple.frontboard.debugapplications` and `com.apple.frontboardservices.display-layout-monitor`. That is what lets it call SpringBoard's `SBSAppResizingService`. Only Apple can sign `com.apple.*` private entitlements into a binary; a developer-signed app that claims one won't install or launch.
- **An app can't reach the resize server (tested).** It is only a local Mach service (`com.apple.coredevice.feature.resizableappmanagement`), and the app sandbox blocks the lookup. A minimal developer-signed test app (Personal Team, iOS 27.0.1), launched with `devicectl device process launch --console`, got:

  | Mach service | `bootstrap_look_up` | XPC message |
  |---|---|---|
  | `com.apple.cfprefsd.daemon` (control: allowed for apps) | `0` (found) | — |
  | `com.example.resizeprobe.does-not-exist` (control: no such service) | `1100` `BOOTSTRAP_NOT_PRIVILEGED` | — |
  | `com.apple.coredevice.feature.resizableappmanagement` | `1100` `BOOTSTRAP_NOT_PRIVILEGED` | `Connection invalid` |
  | `com.apple.coredevice.feature.streamresizabilitystate` | `1100` `BOOTSTRAP_NOT_PRIVILEGED` | `Connection invalid` |

  The phone's kernel log (`pymobiledevice3 syslog live`) names the cause:

  ```text
  kernel{Sandbox}: Sandbox: ResizeProbe(<pid>) deny(1) mach-lookup com.apple.coredevice.feature.resizableappmanagement
  kernel{Sandbox}: Sandbox: ResizeProbe(<pid>) deny(1) mach-lookup com.apple.coredevice.feature.streamresizabilitystate
  ```

  The non-existent name fails the same way, so the sandbox rejects anything off its allowlist before launchd looks the name up. `xpc_connection_create_mach_service` is marked unavailable for iOS in the SDK, but it is present at runtime (the app resolved it with `dlsym`). It just can't get past the sandbox.
- The only other way around this is a jailbreak, which isn't a basis for iDesktop.

## Dead ends, so they aren't retried

- **Display size watching:** Device Hub's resize doesn't change any display's size in `devicectl device info displays`. It changes the app's scene size on the target display.
- **`remotectl relay … com.apple.coredevice.appservice`:** fails with "Unable to connect". `appservice` requires the `com.apple.private.CoreDevice.canInstallCustomerContent` entitlement, which `remotectl` lacks.
- **Service directory on the usual port 58783 inside Xcode's tunnel:** connection refused on iOS 27. Use pymobiledevice3's userspace tunnel instead.
- **macOS iPhone Mirroring:** does reshape the real phone, but it uses Continuity screen sharing (`ScreenContinuityServices`), not CoreDevice/RemoteXPC.
