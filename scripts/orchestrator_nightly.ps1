# orchestrator_nightly.ps1 — one autonomous-maintenance tick.
#
# Runs AFTER slow_tier_nightly.ps1 (so F1 labels exist). Cheap by design:
# scan enqueues route jobs, the worker drains pending jobs (heavy exec gated by
# HFT3_ORCH_EXEC), then status is logged. Autonomy is OFF by default, so with no
# config + env the scan is a logged no-op — safe to schedule immediately.
param(
    [switch]$Exec,   # set to also run heavy jobs (exports HFT3_ORCH_EXEC=1)
    [switch]$Sweep   # set to run the discovery sweep + survivor intake before the maintenance tick
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$repo;$repo/packages"
if ($Exec) { $env:HFT3_ORCH_EXEC = "1" }
# Lake roots must be Machine-scope for a SYSTEM scheduled task; default here as a
# fallback so a manually-run tick still resolves the lake.
if (-not $env:HFT3_NPZ_ROOT)     { $env:HFT3_NPZ_ROOT     = "C:/hft3-lake/npz" }
if (-not $env:HFT3_FEATURE_ROOT)  { $env:HFT3_FEATURE_ROOT  = "C:/hft3-lake/features" }
if (-not $env:HFT3_MANIFEST_PATH) { $env:HFT3_MANIFEST_PATH = "C:/hft3-lake/manifest.parquet" }

$stamp = (Get-Date).ToString("yyyyMMdd")
$logDir = Join-Path $repo "runtime/lifecycle"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "orchestrator_nightly_$stamp.log"

"=== orchestrator nightly $stamp ===" | Tee-Object -FilePath $log

# --- Discovery (gated): screen the universe, mint CERTIFIED survivors into the registry ---
if ($Sweep) {
    $sweepOut = Join-Path $repo "research_cards/universe_nightly_$stamp"
    Write-Host "sweep (discovery)..."
    python scripts/run_event_universe.py --out $sweepOut 2>&1 | Tee-Object -FilePath $log -Append
    $universe = Join-Path $sweepOut "universe_result.json"
    if (Test-Path $universe) {
        Write-Host "survivor intake..."
        python -m lifecycle_orchestrator.src.survivor_intake $universe 2>&1 | Tee-Object -FilePath $log -Append
    } else {
        "sweep produced no universe_result.json; skipping intake" | Tee-Object -FilePath $log -Append
    }
}

Write-Host "scan..."
python -m lifecycle_orchestrator.src.orchestrator scan 2>&1 | Tee-Object -FilePath $log -Append
Write-Host "worker..."
python -m lifecycle_orchestrator.src.worker 2>&1 | Tee-Object -FilePath $log -Append
Write-Host "status..."
python -m lifecycle_orchestrator.src.orchestrator status 2>&1 | Tee-Object -FilePath $log -Append
"=== done ===" | Tee-Object -FilePath $log -Append
