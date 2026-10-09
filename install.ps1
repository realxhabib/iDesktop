# iDesktop installer for Windows 10/11.
#
#   irm https://raw.githubusercontent.com/realxhabib/iDesktop/main/install.ps1 | iex
#
# Re-run the same command to update. Installs per-user (no admin needed) into
# %LOCALAPPDATA%\iDesktop and adds Start Menu + desktop shortcuts.
# Options (environment variables, set before running):
#   IDESKTOP_REF       branch or tag to install (default: main)
#   IDESKTOP_NO_APPLE  skip installing Apple Devices
#   IDESKTOP_NO_SHORTCUT  skip the desktop shortcut

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is ~10x faster without the bar
$Repo = 'realxhabib/iDesktop'
$Ref = if ($env:IDESKTOP_REF) { $env:IDESKTOP_REF } else { 'main' }
$Root = Join-Path $env:LOCALAPPDATA 'iDesktop'
$App = Join-Path $Root 'app'
$Venv = Join-Path $Root 'venv'

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Note($t) { Write-Host "    $t" -ForegroundColor DarkGray }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "`n$t" -ForegroundColor Red; throw $t }

Write-Host "iDesktop installer" -ForegroundColor White

# ---------------------------------------------------------------- Windows
if ([Environment]::OSVersion.Version.Build -lt 17763 -or -not [Environment]::Is64BitOperatingSystem) {
    Fail "iDesktop needs 64-bit Windows 10 (1809) or Windows 11."
}

# ---------------------------------------------------------------- Python
function Find-Python {
    # Probing writes to stderr when a version is missing; in Windows PowerShell 5.1 that would
    # stop the script under ErrorActionPreference=Stop, so relax it for the probe.
    $ErrorActionPreference = 'Continue'
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.14', '3.11') {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() }
        }
    }
    $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
    foreach ($c in $cands) {
        $ok = & $c -c "import sys; print(int((3,11) <= sys.version_info[:2] < (3,15)))" 2>$null
        if ($ok -eq '1') { return $c }
    }
    return $null
}

Step "Checking Python (3.11 - 3.14)"
$Py = Find-Python
if (-not $Py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Python 3.13 is needed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this installer again."
    }
    Note "Installing Python 3.13 for this user (winget)..."
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $Py = Find-Python
    if (-not $Py) { Fail "Python installed, but couldn't be found. Open a new PowerShell window and run the installer again." }
}
Note "Using $Py"

# ---------------------------------------------------------------- source
Step "Downloading iDesktop ($Ref)"
New-Item -ItemType Directory -Force $Root | Out-Null
$local = $PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'idesktop\__main__.py'))
if ($local) {
    Note "Installing from this folder: $PSScriptRoot"
    $src = $PSScriptRoot
} else {
    $zip = Join-Path $env:TEMP "idesktop-$([guid]::NewGuid()).zip"
    $kind = if ($Ref -match '^v\d') { 'tags' } else { 'heads' }
    Invoke-WebRequest "https://github.com/$Repo/archive/refs/$kind/$Ref.zip" -OutFile $zip -UseBasicParsing
    $tmp = Join-Path $env:TEMP "idesktop-$([guid]::NewGuid())"
    Expand-Archive $zip $tmp -Force
    Remove-Item $zip
    $src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
}
if ((Resolve-Path $src).Path -ne $App) {
    if (Test-Path $App) { Remove-Item $App -Recurse -Force }
    New-Item -ItemType Directory -Force $App | Out-Null
    foreach ($item in 'idesktop', 'assets', 'tools', 'requirements.txt', 'LICENSE', 'README.md', 'extras', 'uninstall.ps1') {
        $p = Join-Path $src $item
        if (Test-Path $p) { Copy-Item $p $App -Recurse -Force }
    }
}
if (-not $local) { Remove-Item (Split-Path $src) -Recurse -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- venv + packages
Step "Installing Python packages (first run: a minute or two)"
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) { & $Py -m venv $Venv }
$VPy = Join-Path $Venv 'Scripts\python.exe'
& $VPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $VPy -m pip install -r (Join-Path $App 'requirements.txt') --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Package install failed (see the pip output above)." }

# ---------------------------------------------------------------- Apple device service
Step "Checking Apple's device service"
$apple = (Get-AppxPackage AppleInc.AppleDevices -ErrorAction SilentlyContinue) -or
         (Get-AppxPackage AppleInc.iTunes -ErrorAction SilentlyContinue) -or
         (Get-Service 'Apple Mobile Device Service' -ErrorAction SilentlyContinue)
if ($apple) {
    Note "Found (Apple Devices or iTunes)."
} elseif ($env:IDESKTOP_NO_APPLE) {
    Warn "Skipped. Install 'Apple Devices' from the Microsoft Store before using iDesktop."
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Note "Installing Apple Devices from the Microsoft Store (provides the iPhone USB driver)..."
    winget install --id 9NP83LWLPZ9K --source msstore --accept-package-agreements --accept-source-agreements | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn "Couldn't install it automatically. Get 'Apple Devices' from the Microsoft Store." }
} else {
    Warn "Install 'Apple Devices' from the Microsoft Store (it provides the iPhone USB driver)."
}

# ---------------------------------------------------------------- HEVC
Step "Checking HEVC video support"
if (Get-AppxPackage *HEVCVideoExtension* -ErrorAction SilentlyContinue) {
    Note "HEVC Video Extensions found."
} else {
    Warn "HEVC Video Extensions aren't installed; the HD mirror needs them (Microsoft Store, ~1 USD)."
    Warn "Some PCs decode HEVC without them - iDesktop tells you on first start if yours can't."
    $a = Read-Host "    Open the Store page now? [Y/n]"
    if ($a -notmatch '^[nN]') { Start-Process 'ms-windows-store://pdp/?ProductId=9NMZLZ57R3T7' }
}

# ---------------------------------------------------------------- shortcuts
Step "Creating shortcuts"
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut($path) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
    $s.Arguments = '-m idesktop'
    $s.WorkingDirectory = $App
    $s.IconLocation = (Join-Path $App 'assets\idesktop.ico')
    $s.Description = 'Mirror and control your iPhone'
    $s.Save()
}
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-Shortcut (Join-Path $startMenu 'iDesktop.lnk')
if (-not $env:IDESKTOP_NO_SHORTCUT) { New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'iDesktop.lnk') }
Note "Start Menu and desktop: iDesktop"

Write-Host "`niDesktop is installed." -ForegroundColor Green
Write-Host @"

  1. Plug your iPhone in with a cable and unlock it.
  2. Open iDesktop (Start Menu or desktop shortcut).
  3. The first time, iDesktop walks you through tapping Trust and turning on Developer Mode.

  After one wired start, iDesktop also works over Wi-Fi with no cable.
  Uninstall: run uninstall.ps1 in $App, or
    irm https://raw.githubusercontent.com/$Repo/main/uninstall.ps1 | iex
"@
