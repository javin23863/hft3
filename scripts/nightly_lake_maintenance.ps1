# Nightly lake maintenance: refresh hash catalog -> sync lake to B2 -> health check.
# Registered as Windows scheduled task "hft3-lake-nightly" (02:30 local).
$ErrorActionPreference = "Continue"
$repo = "C:\Users\MSI\repos\hft3"
$py = "C:\Users\MSI\AppData\Local\Programs\Python\Python312\python.exe"
$log = Join-Path $repo "runtime\lake_nightly.log"

$env:HFT3_NPZ_ROOT = "C:\hft3-lake\npz"
$env:HFT3_FEATURE_ROOT = "C:\hft3-lake\features"
$env:HFT3_MANIFEST_PATH = "C:\hft3-lake\manifest.parquet"
$env:PYTHONPATH = "$repo;$repo\packages"

function Log([string]$msg) { Add-Content $log "$(Get-Date -Format o) $msg" }

Log "=== nightly start"
& $py (Join-Path $repo "scripts\build_lake_catalog.py") *>> $log
Log "catalog rc=$LASTEXITCODE"
& pwsh -NoProfile -File (Join-Path $repo "scripts\sync_lake_b2.ps1") -Stage all *>> $log
Log "sync rc=$LASTEXITCODE"
& pwsh -NoProfile -File (Join-Path $repo "scripts\archive_chi404_capture.ps1") *>> $log
Log "capture-archive rc=$LASTEXITCODE"
& $py (Join-Path $repo "scripts\data_doctor.py") *>> $log
Log "doctor rc=$LASTEXITCODE"
Log "=== nightly done"
