# Install ponytail (lazy-senior-dev agent discipline) into hft3
$ErrorActionPreference = "Stop"
$Repo = if ($env:HFT3_REPO) { $env:HFT3_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$Vendor = Join-Path $Repo "vendor\ponytail"
$RuleSrc = Join-Path $Vendor ".cursor\rules\ponytail.mdc"
$RuleDst = Join-Path $Repo ".cursor\rules\ponytail.mdc"

if (-not (Test-Path $Vendor)) {
    git clone https://github.com/DietrichGebert/ponytail.git $Vendor
} else {
    git -C $Vendor pull --ff-only
}

if (-not (Test-Path $RuleSrc)) { throw "ponytail rule missing at $RuleSrc" }
Copy-Item -Force $RuleSrc $RuleDst
Write-Host "OK: ponytail at $Vendor; Cursor rule -> $RuleDst"
Write-Host "Charter: docs/ai/PONYTAIL.md"
