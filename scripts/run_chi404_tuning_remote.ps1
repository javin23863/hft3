# Deploy infrastructure and run CHI404 tuning with reboot handling.
$ErrorActionPreference = "Stop"
$Repo = "C:\Users\MSI\Documents\GitHub\hft3"
$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")

Write-Host "Syncing infrastructure to chi404..."
ssh chi404 "mkdir -p /root/hft3/repo /root/hft3/logs/tuning"
scp -r "$Repo\infrastructure" chi404:/root/hft3/repo/

$remote = @"
export RUN_ID=$RunId
export HFT3_TUNING_RESUME_STEP=${env:HFT3_TUNING_RESUME_STEP:-0}
chmod +x /root/hft3/repo/infrastructure/chi404/*.sh /root/hft3/repo/infrastructure/*.sh
chmod +x /root/hft3/repo/infrastructure/chi404/validate_pass_criteria.py
bash /root/hft3/repo/infrastructure/chi404/run_chi404_tuning.sh
"@

Write-Host "Starting tuning RUN_ID=$RunId step=${env:HFT3_TUNING_RESUME_STEP:-0}"
ssh chi404 $remote
$ec = $LASTEXITCODE
if ($ec -eq 0) {
  Write-Host "Tuning complete."
} else {
  Write-Host "Exit $ec — if server rebooted, wait 60s then: `$env:HFT3_TUNING_RESUME_STEP=2; .\scripts\run_chi404_tuning_remote.ps1"
  Write-Host "After kernel reboot use HFT3_TUNING_RESUME_STEP=4"
}
