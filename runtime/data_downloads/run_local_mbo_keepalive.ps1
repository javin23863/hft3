$Repo = 'c:\Users\MSI\Documents\GitHub\hft3'
Set-Location $Repo
while ($true) {
  Write-Host "[keepalive] local shard 0/2 workers=64 started=$(Get-Date -Format o)"
  python scripts/download_mbo_release_data.py --download --derive-npz --scope full_catalog --priority-events --workers 64 --shard-index 0 --shard-count 2 --output runtime/data_downloads/mbo_download_report_local.json 2>&1 | Tee-Object -FilePath runtime/data_downloads/full_catalog_local.log -Append
  Write-Host "[keepalive] exit $LASTEXITCODE, retry in 30s"
  Start-Sleep -Seconds 30
}
