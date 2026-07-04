# Phase 1: core 12 symbols (9 equity+rates + GC/SI/CL) -> B2 core12/
# "Continue" not "Stop": databento prints BentoWarning to stderr; under PS 5.1 with
# EAP=Stop the 2>&1 redirect turns that into a terminating NativeCommandError.
$ErrorActionPreference = "Continue"
$env:HFT3_NPZ_ROOT = "C:\hft3-lake\npz"
$env:HFT3_MANIFEST_PATH = "C:\hft3-lake\manifest.parquet"
Set-Location "C:\Users\MSI\repos\hft3"
python runtime/databento/stream_mbo_full_to_b2.py `
  --stype-in continuous `
  --chunk week `
  --start 2017-05-21 `
  --symbols-file runtime/databento/glbx_core12_symbols.json `
  --remote-subdir core12 `
  --log-file runtime/databento/mbo_core12_stream.log `
  2>&1 | Tee-Object -FilePath runtime/databento/mbo_core12_stream.console.log -Append
