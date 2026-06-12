# install_cockpit_shortcut.ps1 — create a Desktop shortcut that launches the cockpit.
# Idempotent: re-running overwrites the existing shortcut.
param(
    [string]$Name = "HFT3 Cockpit"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $repo "scripts/cockpit_launch.ps1"

# Prefer PowerShell 7 (pwsh) if installed, else Windows PowerShell.
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
$shell = if ($pwsh) { $pwsh.Source } else { Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe" }

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "$Name.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnkPath)
$sc.TargetPath = $shell
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$launcher`""
$sc.WorkingDirectory = $repo
$sc.IconLocation = "$($env:SystemRoot)\System32\imageres.dll,144"  # line-chart style icon
$sc.Description = "Launch the HFT3 Cockpit trader dashboard (loopback)"
$sc.WindowStyle = 7  # minimized console; browser is the real UI
$sc.Save()

Write-Host "Shortcut created: $lnkPath"
Write-Host "Target: $shell"
Write-Host "Launcher: $launcher"
