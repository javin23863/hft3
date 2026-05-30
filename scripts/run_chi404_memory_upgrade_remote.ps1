# Deploy infrastructure and run CHI404 memory upgrade (restore + PDF gap-fill).
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RunId = if ($env:RUN_ID) { $env:RUN_ID } else { (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") }
$Resume = if ($env:HFT3_MEMORY_RESUME_STEP) { $env:HFT3_MEMORY_RESUME_STEP } else { "0" }
$RestoreId = if ($env:RESTORE_ID) { $env:RESTORE_ID } else { "" }

if ($Resume -eq "4" -and -not $env:RUN_ID) {
  Write-Error "HFT3_MEMORY_RESUME_STEP=4 requires RUN_ID from the interrupted run (same log dir)."
}
if ($Resume -eq "4" -and -not $RestoreId) {
  Write-Warning "RESTORE_ID not set - orchestrator will load from RESTORE_ID.txt if RUN_ID log dir exists."
}

Write-Host "Syncing infrastructure to chi404..."
ssh chi404 "mkdir -p /root/hft3/repo /root/hft3/logs/memory_upgrade /root/hft3/restore_points"
scp -r "$Repo\infrastructure" chi404:/root/hft3/repo/

$restoreEnv = if ($RestoreId) { "export RESTORE_ID=$RestoreId" } else { "" }
$remote = @"
export RUN_ID=$RunId
export HFT3_MEMORY_RESUME_STEP=$Resume
$restoreEnv
chmod +x /root/hft3/repo/infrastructure/chi404/*.sh /root/hft3/repo/infrastructure/*.sh
find /root/hft3/repo/infrastructure -name '*.sh' -exec sed -i 's/\r$//' {} +
chmod +x /root/hft3/repo/infrastructure/chi404/validate_pass_criteria.py
bash /root/hft3/repo/infrastructure/chi404/run_chi404_memory_upgrade.sh
"@

Write-Host "Starting memory upgrade RUN_ID=$RunId step=$Resume RESTORE_ID=$RestoreId"
ssh chi404 $remote
$ec = $LASTEXITCODE
if ($ec -eq 0) {
  Write-Host "Memory upgrade complete."
} elseif ($Resume -eq "0") {
  Write-Host "Exit $ec - if server rebooted for GRUB, wait 60s then:"
  Write-Host "  `$env:HFT3_MEMORY_RESUME_STEP=4; `$env:RUN_ID=$RunId; `$env:RESTORE_ID=<from-RESTORE_ID.txt-or-log>; .\scripts\run_chi404_memory_upgrade_remote.ps1"
} else {
  Write-Host "Exit $ec - check logs under /root/hft3/logs/memory_upgrade/$RunId/"
}
