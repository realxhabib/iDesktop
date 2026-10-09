# Remove iDesktop: the app, its Python environment and the shortcuts.
#   irm https://raw.githubusercontent.com/realxhabib/iDesktop/main/uninstall.ps1 | iex
# Settings and logs in %APPDATA%\iDesktop are kept unless IDESKTOP_PURGE=1 is set.
# Apple Devices, Python and the phone's pairing are left alone.

$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:LOCALAPPDATA 'iDesktop'

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Root, 'OrdinalIgnoreCase') } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

foreach ($lnk in (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\iDesktop.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Desktop')) 'iDesktop.lnk')) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force }
}
if (Test-Path $Root) {
    # This script may itself live under $Root\app; PowerShell has it in memory, so that's fine.
    Remove-Item $Root -Recurse -Force
}
if ($env:IDESKTOP_PURGE) {
    $cfg = Join-Path $env:APPDATA 'iDesktop'
    if (Test-Path $cfg) { Remove-Item $cfg -Recurse -Force }
}
Write-Host "iDesktop has been removed." -ForegroundColor Green
