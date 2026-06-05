# Rebuild graphify graph from repo root after code edits.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\graphify'
$LogFile = Join-Path $LogDir 'rebuild.log'
$TimeoutWrapper = Join-Path $RepoRoot 'tools\shell\run_with_timeout.ps1'
$UpdateTimeoutSec = 300
$ClusterTimeoutSec = 120
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LogLine {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $Message"
    $line | Tee-Object -FilePath $LogFile -Append
    return $line
}

Write-LogLine 'graphify rebuild start' | Out-Null

if (-not (Get-Command graphify -ErrorAction SilentlyContinue)) {
    Write-LogLine 'ERROR: graphify not on PATH (pip install graphifyy; see docs/GRAPHIFY_WORKFLOW.md)' | Out-Null
    Write-Error 'graphify not found on PATH'
    exit 1
}
if (-not (Test-Path $TimeoutWrapper)) {
    Write-LogLine "ERROR: timeout wrapper missing: $TimeoutWrapper" | Out-Null
    Write-Error "timeout wrapper missing: $TimeoutWrapper"
    exit 1
}

$updated = $false
$updateExit = 0
try {
    Write-LogLine "graphify update start (timeout ${UpdateTimeoutSec}s)" | Out-Null
    & $TimeoutWrapper -TimeoutSec $UpdateTimeoutSec -Label 'graphify-update' -- graphify update . 2>&1 |
        Tee-Object -FilePath $LogFile -Append
    $updateExit = $LASTEXITCODE
    if ($updateExit -eq 0) {
        $updated = $true
    } else {
        throw "graphify update . exited $updateExit"
    }
} catch {
    $updateMessage = $_.Exception.Message
    if ($updateExit -eq 0) { $updateExit = 1 }
    Write-LogLine "diagnostic fallback: graphify cluster-only . --no-viz ($updateMessage)" | Out-Null
    & $TimeoutWrapper -TimeoutSec $ClusterTimeoutSec -Label 'graphify-cluster-only' -- graphify cluster-only . --no-viz 2>&1 |
        Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        Write-LogLine "graphify rebuild failed (exit $LASTEXITCODE)" | Out-Null
        exit $LASTEXITCODE
    }
    Write-LogLine "graphify update failed before diagnostic fallback (exit $updateExit): $updateMessage" | Out-Null
    exit $updateExit
}

if ($updated) {
    Write-LogLine 'graphify rebuild done (update)' | Out-Null
} else {
    Write-LogLine 'graphify rebuild done (cluster-only)' | Out-Null
}
exit 0
