# Optional extra: WebDriverAgent (pinch zoom, auto-rotate, basic mode during calls,
# "Sound on PC only"). Downloads Appium's prebuilt WebDriverAgent, packs it as an .ipa,
# and walks you through sideloading it with Sideloadly and your Apple ID.
#
#   powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\iDesktop\app\extras\install-wda.ps1"

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WdaVersion = 'v16.12.8'   # appium/WebDriverAgent release
$App = Split-Path $PSScriptRoot
$Py = @((Join-Path $env:LOCALAPPDATA 'iDesktop\venv\Scripts\python.exe'), (Join-Path $App '.venv\Scripts\python.exe')) |
      Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Py) { throw "Run the iDesktop installer first." }

$Out = Join-Path $env:APPDATA 'iDesktop'
New-Item -ItemType Directory -Force $Out | Out-Null
$Ipa = Join-Path $Out 'WebDriverAgent.ipa'
$work = Join-Path $env:TEMP "idesktop-wda-$([guid]::NewGuid())"
New-Item -ItemType Directory -Force $work | Out-Null

Write-Host "==> Downloading WebDriverAgent $WdaVersion (Appium, BSD-3-Clause)" -ForegroundColor Cyan
Invoke-WebRequest "https://github.com/appium/WebDriverAgent/releases/download/$WdaVersion/WebDriverAgentRunner-Runner.zip" `
    -OutFile (Join-Path $work 'wda.zip') -UseBasicParsing
Expand-Archive (Join-Path $work 'wda.zip') (Join-Path $work 'x') -Force
& $Py (Join-Path $App 'tools\make_ipa.py') (Join-Path $work 'x\WebDriverAgentRunner-Runner.app') $Ipa
Remove-Item $work -Recurse -Force

Write-Host @"

==> Built $Ipa

Now sideload it (one time; re-sign when it expires):
  1. Install Sideloadly: https://sideloadly.io  (it opens in your browser now)
  2. Plug in the iPhone, open Sideloadly, drag the .ipa above onto it,
     enter your Apple ID and click Start.
       - Free Apple ID: the app expires after 7 days; just sideload again.
       - Paid developer account: it lasts a year.
  3. On the iPhone: Settings > General > VPN & Device Management > your Apple ID > Trust.
  4. Restart iDesktop. It finds WebDriverAgent by itself.
"@ -ForegroundColor White
Start-Process 'https://sideloadly.io'
Start-Process explorer.exe "/select,`"$Ipa`""
