# orchestrator_nightly.ps1 — one autonomous-maintenance tick.
#
# Runs AFTER slow_tier_nightly.ps1 (so F1 labels exist). Cheap by design:
# scan enqueues route jobs, the worker drains pending jobs (heavy exec gated by
# HFT3_ORCH_EXEC), then status is logged. Autonomy is OFF by default, so with no
# config + env the scan is a logged no-op — safe to schedule immediately.
param(
    [switch]$Exec  # set to also run heavy jobs (exports HFT3_ORCH_EXEC=1)
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$repo;$repo/packages"
if ($Exec) { $env:HFT3_ORCH_EXEC = "1" }

$stamp = (Get-Date).ToString("yyyyMMdd")
$logDir = Join-Path $repo "runtime/lifecycle"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "orchestrator_nightly_$stamp.log"

"=== orchestrator nightly $stamp ===" | Tee-Object -FilePath $log
Write-Host "scan..."
python -m lifecycle_orchestrator.src.orchestrator scan 2>&1 | Tee-Object -FilePath $log -Append
Write-Host "worker..."
python -m lifecycle_orchestrator.src.worker 2>&1 | Tee-Object -FilePath $log -Append
Write-Host "status..."
python -m lifecycle_orchestrator.src.orchestrator status 2>&1 | Tee-Object -FilePath $log -Append
"=== done ===" | Tee-Object -FilePath $log -Append
