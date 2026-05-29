# Discover R|Trader Pro install path and log/export directories on this Windows machine.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutDir = Join-Path $RepoRoot 'logs\rithmic_trial'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutFile = Join-Path $OutDir 'rtrader_discovery.json'

function Find-RTraderExe {
    if ($env:RTRADER_EXE_PATH -and (Test-Path $env:RTRADER_EXE_PATH)) {
        return (Resolve-Path $env:RTRADER_EXE_PATH).Path
    }
    $names = @('RTrader.exe', 'RTraderPro.exe')
    $roots = @(
        'C:\Program Files\Rithmic',
        'C:\Program Files (x86)\Rithmic',
        "$env:LOCALAPPDATA\Programs\Rithmic"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($name in $names) {
            $hit = Get-ChildItem -Path $root -Recurse -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
    }
    return $null
}

$watch = @()
$candidates = @(
    "$env:USERPROFILE\Documents\Rithmic",
    "$env:USERPROFILE\Documents\RTrader Pro",
    "$env:LOCALAPPDATA\Rithmic",
    "$env:LOCALAPPDATA\RTrader Pro",
    "$env:APPDATA\Rithmic",
    "$env:APPDATA\RTrader Pro",
    'C:\Program Files\Rithmic',
    'C:\Program Files (x86)\Rithmic'
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $watch += $p }
}

$payload = @{
    platform = 'windows'
    rithmic_environment = 'Rithmic Paper Trading'
    rithmic_gateway = 'Chicago'
    exe_path = (Find-RTraderExe)
    watch_dirs = $watch
    note = 'Set RTRADER_EXE_PATH and RTRADER_WATCH_DIRS in .env if discovery is incomplete'
}
$payload | ConvertTo-Json -Depth 5 | Set-Content -Path $OutFile -Encoding utf8
Write-Host "Wrote $OutFile"
$payload | ConvertTo-Json -Depth 5
