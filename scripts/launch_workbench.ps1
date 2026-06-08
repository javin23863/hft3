# Launch HFT3 Streamlit workbench from repo root (desktop shortcut target).
param(
    [switch]$SkipBrowser,
    [switch]$SkipPreflight,
    [switch]$PreflightOnly,
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$LogDir = Join-Path $RepoRoot 'runtime/logs'
$LogFile = Join-Path $LogDir 'workbench_launcher.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = (Get-Date).ToString('o')
    $line = "[$ts] [$Level] $Message"
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
    Write-Host $line
}
Write-Log "launcher start repo=$RepoRoot argv=$($MyInvocation.Line)"

Set-Location $RepoRoot
$env:PYTHONPATH = $RepoRoot

# Refuse to launch if the workbench source tree is missing.
$WorkbenchApp = Join-Path $RepoRoot 'apps/workbench/ui/app.py'
if (-not (Test-Path $WorkbenchApp)) {
    Write-Log "workbench app.py missing at $WorkbenchApp" 'ERROR'
    if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
        Read-Host 'Press Enter to close'
    }
    exit 1
}

# Verify we are using the same Python the repo expects (system python 3.12
# at the standard install path; abort if a different one is on PATH).
$ExpectedPy = 'C:\Users\MSI\AppData\Local\Programs\Python\Python312\python.exe'
$ActualPy = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($ActualPy -ne $ExpectedPy) {
    Write-Log "Python on PATH ($ActualPy) does not match expected ($ExpectedPy)" 'WARN'
} else {
    Write-Log "python OK ($ActualPy)"
}

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

if (-not $SkipPreflight) {
    Write-Log "preflight: workbench imports"
    $preflightResult = Invoke-WorkbenchPreflight
    if ($preflightResult.Code -ne 0 -and $preflightResult.ErrorLines -notcontains "missing $(Join-Path $RepoRoot 'scripts/workbench_preflight.py')") {
        Write-Log 'preflight failed; clearing __pycache__ and retrying' 'WARN'
        Get-ChildItem -Path (Join-Path $RepoRoot 'apps/workbench') -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $preflightResult = Invoke-WorkbenchPreflight -ErrorLines $preflightResult.ErrorLines
    }
    if ($preflightResult.Code -ne 0) {
        if ($preflightResult.ErrorLines.Count -gt 0) {
            $preflightResult.ErrorLines | ForEach-Object { Write-Log $_ 'ERROR' }
        }
        Write-Log "preflight FAILED. Diagnose: python scripts/workbench_preflight.py" 'ERROR'
        if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
            Read-Host 'Press Enter to close'
        }
        exit 1
    }

    Write-Log "preflight: grader import tests (advisory, 120s timeout)"
    $graderTimeoutSec = 120
    $graderJob = Start-Job -ScriptBlock {
        & python -m pytest tests/test_workbench/test_ui_imports.py tests/test_workbench/test_event_catalog.py -q --tb=line 2>&1
        exit $LASTEXITCODE
    }
    $graderStart = Get-Date
    while ($graderJob.State -eq 'Running' -and ((Get-Date) - $graderStart).TotalSeconds -lt $graderTimeoutSec) {
        Start-Sleep -Seconds 1
    }
    if ($graderJob.State -eq 'Completed') {
        Remove-Job -Job $graderJob -Force
        Write-Log "grader tests passed"
    } else {
        Write-Log "grader tests did not complete within ${graderTimeoutSec}s (job id=$($graderJob.Id) still running)" 'WARN'
    }
}

if ($PreflightOnly) {
    if ($SkipPreflight) {
        Write-Log "ERROR: -PreflightOnly cannot be used with -SkipPreflight" 'ERROR'
        if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
            Read-Host 'Press Enter to close'
        }
        exit 1
    }
    Write-Log "preflight complete"
    exit 0
}

$latencySummary = Join-Path $RepoRoot 'runtime/latency_reports/latency_summary.json'
if (-not (Test-Path $latencySummary)) {
    Write-Log "missing $latencySummary — backtests need CHI404 latency summary for C++ authority" 'WARN'
}

$npzDir = Join-Path $RepoRoot 'data/npz'
$anyNpz = Get-ChildItem -Path $npzDir -Filter '*_mbo.npz' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $anyNpz) {
    Write-Log "no *_mbo.npz under data/npz — pull macro events from packages/data_system/config/events.csv" 'WARN'
}

$url = "http://localhost:$Port"
Write-Log "starting streamlit at $url"
Write-Host "Starting HFT3 Workbench at $url (repo: $RepoRoot)" -ForegroundColor Cyan

if (-not $SkipBrowser) {
    Start-Process $url
}

& python -m streamlit run apps/workbench/ui/app.py --server.headless true --server.port $Port 2>&1 | ForEach-Object { Write-Log $_ }
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Log "streamlit exited with code $exitCode" 'ERROR'
    if ([Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
        Read-Host 'Press Enter to close'
    }
    exit $exitCode
}
Write-Log "streamlit exited cleanly rc=$exitCode"
