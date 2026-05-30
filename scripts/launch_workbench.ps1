# Launch HFT3 Streamlit workbench from repo root (desktop shortcut target).
param(
    [switch]$SkipBrowser,
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

function Test-CommandOnPath {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandOnPath 'python')) {
    Write-Host 'ERROR: python not on PATH. Install Python 3.12+ and retry.' -ForegroundColor Red
    exit 1
}

$streamlitOk = $false
try {
    & python -c "import streamlit" 2>$null
    if ($LASTEXITCODE -eq 0) { $streamlitOk = $true }
} catch {}

if (-not $streamlitOk) {
    Write-Host 'ERROR: streamlit not installed. Run: pip install -r workbench/requirements.txt' -ForegroundColor Red
    exit 1
}

$latencySummary = Join-Path $RepoRoot 'runtime/latency_reports/latency_summary.json'
if (-not (Test-Path $latencySummary)) {
    Write-Host "WARN: missing $latencySummary — backtests need CHI404 latency summary for C++ authority." -ForegroundColor Yellow
}

$cpiNpz = Join-Path $RepoRoot 'data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz'
if (-not (Test-Path $cpiNpz)) {
    Write-Host "WARN: missing $cpiNpz — local CPI backtests unavailable until NPZ is present." -ForegroundColor Yellow
}

$url = "http://localhost:$Port"
Write-Host "Starting HFT3 Workbench at $url (repo: $RepoRoot)" -ForegroundColor Cyan

if (-not $SkipBrowser) {
    Start-Process $url
}

& python -m streamlit run workbench/ui/app.py --server.headless true --server.port $Port
