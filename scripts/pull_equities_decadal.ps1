# Pull decadal low-float MBO sessions (budget-gated)
# Requires DATABENTO_API_KEY in environment or .env

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $env:DATABENTO_API_KEY) {
    if (Test-Path ".env") {
        Get-Content ".env" | ForEach-Object {
            if ($_ -match '^\s*DATABENTO_API_KEY=(.+)$') {
                $env:DATABENTO_API_KEY = $matches[1].Trim()
            }
        }
    }
}

if (-not $env:DATABENTO_API_KEY) {
    Write-Error "DATABENTO_API_KEY not set. Add to .env or environment."
}

Write-Host "=== Decadal cost estimate (MBO + daily OHLCV + OPRA options) ==="
python -m equities_lane.pipeline estimate-decadal `
    --decadal-config packages/equities_lane/config/decadal_runners.yaml

Write-Host ""
Write-Host "=== Pulling full catalog (MBO L3 + daily + OPRA, resume, override budget gates) ==="
python -m equities_lane.pipeline pull-decadal `
    --decadal-config packages/equities_lane/config/decadal_runners.yaml `
    --resume `
    --pull-options `
    --override-hard-limit `
    --override-operating-cap

Write-Host "Done. Manifest: data/equities/manifest/decadal_pull.json"
