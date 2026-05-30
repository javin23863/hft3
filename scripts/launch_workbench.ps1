# Launch HFT3 Streamlit workbench from repo root (desktop shortcut target).
param(
    [switch]$SkipBrowser,
    [switch]$SkipPreflight,
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

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
    Exit-Launcher -Message 'ERROR: streamlit not installed. Run: pip install -r workbench/requirements.txt'
}

function Invoke-WorkbenchPreflight {
    $script = @"
import sys
from pathlib import Path
root = Path(r'$RepoRoot')
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition
from workbench.src.registry.model_catalog import load_catalog
from workbench.ui.campaign_panel import get_session_composition
assert load_catalog()
print('workbench import OK')
"@
    & python -c $script
    return $LASTEXITCODE
}

if (-not $SkipPreflight) {
    Write-Host 'Preflight: workbench imports...' -ForegroundColor DarkCyan
    $preflightOk = Invoke-WorkbenchPreflight
    if ($preflightOk -ne 0) {
        Write-Host 'Preflight failed; clearing workbench __pycache__ and retrying once...' -ForegroundColor Yellow
        Get-ChildItem -Path (Join-Path $RepoRoot 'workbench') -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $preflightOk = Invoke-WorkbenchPreflight
    }
    if ($preflightOk -ne 0) {
        Exit-Launcher -Message @(
            'ERROR: workbench import failed (CatalogEntry / model_catalog / campaign_panel).',
            'Try: git pull; pip install -r workbench/requirements.txt'
        )
    }

    Write-Host 'Preflight: grader import tests...' -ForegroundColor DarkCyan
    & python -m pytest tests/test_workbench/test_ui_imports.py tests/test_workbench/test_event_catalog.py -q --tb=line
    if ($LASTEXITCODE -ne 0) {
        Exit-Launcher -Message 'ERROR: workbench grader import tests failed. Run: powershell -File scripts/verify_workbench.ps1'
    }
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
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Exit-Launcher -Code $exitCode -Message "Streamlit exited with code $exitCode"
}
