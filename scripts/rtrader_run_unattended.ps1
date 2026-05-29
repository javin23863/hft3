# Headless/unattended Rithmic trial capture on this Windows machine (R|Trader Pro + file tail).
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Import-DotEnv($path) {
    if (-not (Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $i = $line.IndexOf('=')
        if ($i -lt 1) { return }
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($k, $v, 'Process')
    }
}

Import-DotEnv (Join-Path $RepoRoot '.env')

$Config = if ($env:RITHMIC_TRIAL_CONFIG) { $env:RITHMIC_TRIAL_CONFIG } else { 'data_system/config/rithmic_trial_windows.yaml' }
$env:RITHMIC_TRIAL_ENABLED = '1'
$env:RITHMIC_TRIAL_CONNECTOR = 'rtrader'
if (-not $env:RITHMIC_ENVIRONMENT) { $env:RITHMIC_ENVIRONMENT = 'Rithmic Paper Trading' }
if (-not $env:RITHMIC_GATEWAY) { $env:RITHMIC_GATEWAY = 'Chicago' }

& (Join-Path $RepoRoot 'scripts\rtrader_discover_windows.ps1') | Out-Null
$disc = Get-Content (Join-Path $RepoRoot 'logs\rithmic_trial\rtrader_discovery.json') -Raw | ConvertFrom-Json
if ($disc.exe_path -and -not $env:RTRADER_EXE_PATH) {
    $env:RTRADER_EXE_PATH = $disc.exe_path
}
if ($disc.watch_dirs.Count -gt 0 -and -not $env:RTRADER_WATCH_DIRS) {
    $env:RTRADER_WATCH_DIRS = ($disc.watch_dirs -join ';')
}

$LogDir = Join-Path $RepoRoot 'logs\rithmic_trial'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$DaemonLog = Join-Path $LogDir 'unattended_daemon.log'

Write-Host "Starting unattended capture (Paper Trading / Chicago Gateway)"
Write-Host "Config: $Config"
Write-Host "Log: $DaemonLog"
Write-Host "Stop with Ctrl+C or: Stop-Process -Name python -Force (if only this job)"

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw 'python not found on PATH' }

Start-Process -FilePath $py -ArgumentList @(
    '-m', 'data_system.rithmic_trial.pipeline', 'run-unattended',
    '--config', $Config
) -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $DaemonLog -RedirectStandardError $DaemonLog

Write-Host "Daemon started in background. Tail log:"
Write-Host "  Get-Content -Wait $DaemonLog"
