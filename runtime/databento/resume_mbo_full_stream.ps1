# Resume existing lake/databento_mbo_full stream (continuous stype fix).
$ErrorActionPreference = "Stop"
$env:HFT3_NPZ_ROOT = "C:\hft3-lake\npz"
$env:HFT3_MANIFEST_PATH = "C:\hft3-lake\manifest.parquet"
Set-Location "C:\Users\MSI\repos\hft3"
$log = "runtime\databento\mbo_full_stream.log"
python runtime/databento/stream_mbo_full_to_b2.py --stype-in continuous --chunk week --start 2017-05-21 2>&1 |
    Tee-Object -FilePath $log -Append
