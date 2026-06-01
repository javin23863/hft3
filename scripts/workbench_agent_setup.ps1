# Launch HFT3 workbench agent setup — full automated onboarding from a freshly cloned repo.
param(
    [switch]$SkipDownload,
    [switch]$SkipGraphRebuild,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$env:PYTHONPATH = "$RepoRoot;$RepoRoot\packages;$RepoRoot\apps"

function Write-Step { param([string]$Msg) Write-Host "--- $Msg ---" -ForegroundColor Cyan }

Write-Step "python -m workbench setup"
& python -m workbench setup --rebuild-graph
if ($LASTEXITCODE -ne 0 -and -not $SkipDownload) {
    Write-Step "Installing workbench dependencies"
    & pip install -r apps/workbench/requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed; continuing..." -ForegroundColor Yellow }
}

Write-Step "python -m workbench verify"
& python -m workbench verify
if ($LASTEXITCODE -ne 0) {
    Write-Host "Verification warnings present — review output above." -ForegroundColor Yellow
}

if (-not $SkipGraphRebuild) {
    Write-Step "Rebuilding graphify"
    $graphRebuild = Join-Path $RepoRoot 'scripts\graphify_rebuild.ps1'
    if (Test-Path $graphRebuild) {
        & powershell -File $graphRebuild
    }
}

Write-Step "Running agent verification gate"
$verifyScript = Join-Path $RepoRoot 'scripts\run_agent_verify.ps1'
if (Test-Path $verifyScript) {
    & powershell -File $verifyScript
}

Write-Step "python -m workbench status"
& python -m workbench status
Write-Host "Agent setup complete." -ForegroundColor Green
