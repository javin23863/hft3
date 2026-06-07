# Restart macro_releases MBO download until scope completes or budget exhausted.
param(
    [int]$Workers = 16
)

$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo

$Log = Join-Path $Repo "runtime/data_downloads/macro_releases_download.log"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

while ($true) {
    Write-Host "[keepalive $Stamp] starting download workers=$Workers"
    python scripts/download_mbo_release_data.py `
        --download `
        --derive-npz `
        --scope macro_releases `
        --workers $Workers `
        --output "runtime/data_downloads/mbo_download_report.json" `
        2>&1 | Tee-Object -FilePath $Log -Append
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host "[keepalive] download finished OK"
        break
    }
    Write-Host "[keepalive] exit $code - retry in 30s"
    Start-Sleep -Seconds 30
}
