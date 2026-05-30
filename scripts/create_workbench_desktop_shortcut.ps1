# Create HFT3 Workbench desktop shortcut (idempotent — overwrites existing .lnk).
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $RepoRoot 'scripts/launch_workbench.ps1'
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'HFT3 Workbench.lnk'

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = 'powershell.exe'
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = 'hft3 microstructure backtest workbench (Streamlit)'

$iconPath = Join-Path $env:SystemRoot 'System32\imageres.dll'
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pythonExe) {
    $Shortcut.IconLocation = "$pythonExe,0"
} else {
    $Shortcut.IconLocation = "$iconPath,109"
}

$Shortcut.Save()
Write-Host "Created shortcut: $ShortcutPath" -ForegroundColor Green
