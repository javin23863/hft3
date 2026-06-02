# Smart research data download orchestrator
# Phase A (default): audit, fill imbalance gaps if any, estimate — no spend on pull-decadal
# Phase B (-ConfirmPull): pull-decadal --resume --pull-options + options retries + final audit

param(
    [switch]$ConfirmPull,
    [double]$MaxCostUsd = 200.0,
    [switch]$SkipImbalanceDownload
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $env:DATABENTO_API_KEY) {
    if (Test-Path ".env") {
        Get-Content ".env" | ForEach-Object {
            if ($_ -match '^\s*DATABENTO_API_KEY=(.+)$') {
                $env:DATABENTO_API_KEY = $matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
}

if (-not $env:DATABENTO_API_KEY) {
    Write-Error "DATABENTO_API_KEY not set. Add to .env or environment."
}

$pyArgs = @(
    "scripts/download_all_research_data.py",
    "--max-cost-usd", $MaxCostUsd
)
if ($ConfirmPull) { $pyArgs += "--confirm-pull" }
if ($SkipImbalanceDownload) { $pyArgs += "--skip-imbalance-download" }

python @pyArgs
exit $LASTEXITCODE
