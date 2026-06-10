# Launch HFT3 Streamlit workbench from repo root (desktop shortcut target).
param(
    [switch]$SkipBrowser,
    [switch]$PreflightOnly,
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$env:PYTHONPATH = "$RepoRoot;$RepoRoot\packages;$RepoRoot\apps"

function Exit-Launcher {
    param(
        [int]$Code = 1,
        [string]$Message = ''
    )
    if ($Message) {
        Write-Host $Message -ForegroundColor Red
    }
    if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
        Read-Host 'Press Enter to close'
    }
    exit $Code
}

function Test-CommandOnPath {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandOnPath 'python')) {
    Exit-Launcher -Message 'ERROR: python not on PATH. Install Python 3.12+ and retry.'
}

$streamlitOk = $false
try {
    & python -c "import streamlit" 2>$null
    if ($LASTEXITCODE -eq 0) { $streamlitOk = $true }
} catch {}

if (-not $streamlitOk) {
    Exit-Launcher -Message 'ERROR: streamlit not installed. Run: pip install -r apps/workbench/requirements.txt'
}

function Invoke-WorkbenchPreflight {
    param([string[]]$ErrorLines = @())

    $preflightScript = Join-Path $RepoRoot 'scripts/workbench_preflight.py'
    if (-not (Test-Path $preflightScript)) {
        Write-Host "ERROR: missing preflight script: $preflightScript" -ForegroundColor Red
        return @{ Code = 1; ErrorLines = @("missing $preflightScript") }
    }

    $attemptOutput = & python $preflightScript 2>&1
    $code = $LASTEXITCODE
    $lines = @($attemptOutput | ForEach-Object { "$_" })
    if ($code -ne 0) {
        $ErrorLines += $lines
    }
    return @{ Code = $code; ErrorLines = $ErrorLines }
}

Write-Host 'Preflight: workbench imports...' -ForegroundColor DarkCyan
$preflightResult = Invoke-WorkbenchPreflight
if ($preflightResult.Code -ne 0 -and $preflightResult.ErrorLines -notcontains "missing $(Join-Path $RepoRoot 'scripts/workbench_preflight.py')") {
    Write-Host 'Preflight failed; clearing workbench __pycache__ and retrying once...' -ForegroundColor Yellow
    Get-ChildItem -Path (Join-Path $RepoRoot 'apps\workbench') -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    $preflightResult = Invoke-WorkbenchPreflight -ErrorLines $preflightResult.ErrorLines
}
if ($preflightResult.Code -ne 0) {
    if ($preflightResult.ErrorLines.Count -gt 0) {
        Write-Host '--- Python preflight error ---' -ForegroundColor Red
        $preflightResult.ErrorLines | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        Write-Host '------------------------------' -ForegroundColor Red
    }
    Exit-Launcher -Message @(
        'ERROR: workbench import failed (CatalogEntry / model_catalog / campaign_panel).',
        'Try: git pull; pip install -r apps/workbench/requirements.txt',
        'Diagnostics: python scripts/workbench_preflight.py'
    )
}

Write-Host 'Preflight: grader import tests...' -ForegroundColor DarkCyan
& python -m pytest tests/test_workbench/test_ui_imports.py tests/test_workbench/test_event_catalog.py -q --tb=line
if ($LASTEXITCODE -ne 0) {
    Exit-Launcher -Message 'ERROR: workbench grader import tests failed. Run: powershell -File scripts/verify_workbench.ps1'
}

if ($PreflightOnly) {
    Write-Host 'Preflight complete.' -ForegroundColor Green
    exit 0
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

& python -m streamlit run apps/workbench/ui/app.py --server.headless true --server.port $Port
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Exit-Launcher -Code $exitCode -Message "Streamlit exited with code $exitCode"
}
