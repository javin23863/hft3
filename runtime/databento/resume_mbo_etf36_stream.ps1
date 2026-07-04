# US ETF MBO lane (36 symbols) -> B2 etf36/ via DBEQ.BASIC
# "Continue" not "Stop": databento prints BentoWarning to stderr; under PS 5.1 with
# EAP=Stop the 2>&1 redirect turns that into a terminating NativeCommandError.
$ErrorActionPreference = "Continue"
$env:HFT3_NPZ_ROOT = "C:\hft3-lake\npz"
$env:HFT3_MANIFEST_PATH = "C:\hft3-lake\manifest.parquet"
Set-Location "C:\Users\MSI\repos\hft3"
python runtime/databento/stream_mbo_full_to_b2.py `
  --dataset DBEQ.BASIC `
  --stype-in raw_symbol `
  --chunk week `
  --start 2023-03-28 `
  --symbols-file runtime/databento/us_etf_mbo_symbols.json `
  --remote-subdir etf36 `
  --log-file runtime/databento/mbo_etf36_stream.log `
  2>&1 | Tee-Object -FilePath runtime/databento/mbo_etf36_stream.console.log -Append
