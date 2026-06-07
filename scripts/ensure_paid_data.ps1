# Non-destructive paid-data sync into the active repo clone.
# 1) Dry-run inventory  2) Copy missing files  3) Write runtime audit report

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "=== Paid data dry-run ==="
python scripts/paid_data_inventory.py --dry-run
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== Paid data sync ==="
python scripts/paid_data_inventory.py --sync
exit $LASTEXITCODE
