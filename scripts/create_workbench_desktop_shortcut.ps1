# Create HFT3 Workbench desktop shortcut (idempotent — overwrites existing .lnk).
param(
    [switch]$AllUsersDesktop
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $RepoRoot 'scripts/launch_workbench.ps1'

if (-not (Test-Path $Launcher)) {
    throw "Launcher missing: $Launcher"
}

if ($AllUsersDesktop) {
    $Desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
} else {
    $Desktop = [Environment]::GetFolderPath('Desktop')
}

$ShortcutPath = Join-Path $Desktop 'HFT3 Workbench.lnk'

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File `"$Launcher`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = 'HFT3 microstructure workbench (Streamlit) — launch_workbench.ps1 with import preflight'

$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pythonExe) {
    $Shortcut.IconLocation = "$pythonExe,0"
} else {
    $iconPath = Join-Path $env:SystemRoot 'System32\imageres.dll'
    $Shortcut.IconLocation = "$iconPath,109"
}

$Shortcut.Save()
Write-Host "Created shortcut: $ShortcutPath" -ForegroundColor Green
Write-Host "Target: $Launcher" -ForegroundColor DarkGray
