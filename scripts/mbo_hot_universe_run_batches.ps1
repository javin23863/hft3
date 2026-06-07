# Run all six HOT-universe MBO backfill batches (~$104 each).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$keyLine = Select-String -Path ".env" -Pattern "^DATABENTO_API_KEY=" | Select-Object -First 1
if ($keyLine) {
    $env:DATABENTO_API_KEY = $keyLine.Line.Split("=", 2)[1].Trim()
}

for ($batch = 1; $batch -le 6; $batch++) {
    Write-Host "=== HOT MBO batch $batch / 6 ==="
    python scripts/mbo_hot_universe_backfill.py --batch $batch --download --max-cost-usd 104 --run-id "mbo_hot_universe_batch$batch"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "batch $batch finished with exit code $LASTEXITCODE"
    }
    python packages/hfc3/audits/mbo_inventory.py
}

Write-Host "All batches complete."
