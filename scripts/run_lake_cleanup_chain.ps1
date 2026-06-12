# One-shot lake cleanup chain: double-named fix -> nested adjudication -> catalog refresh.
$ErrorActionPreference = "Continue"
$repo = "C:\Users\MSI\repos\hft3"
$py = "C:\Users\MSI\AppData\Local\Programs\Python\Python312\python.exe"
$env:HFT3_NPZ_ROOT = "C:\hft3-lake\npz"
$env:PYTHONPATH = "$repo;$repo\packages"
Set-Location $repo

& $py -u scripts\fix_double_named_npz.py 2>&1 | Out-File "$repo\runtime\fix_double.log" -Encoding utf8
& $py -u scripts\adjudicate_nested_npz.py 2>&1 | Out-File "$repo\runtime\adjudicate_nested.log" -Encoding utf8
& $py -u scripts\build_lake_catalog.py 2>&1 | Out-File "$repo\runtime\catalog_refresh.log" -Encoding utf8
Add-Content "$repo\runtime\catalog_refresh.log" "CHAIN DONE"
