# Rebuild graphify graph from repo root after code edits.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\graphify'
$LogFile = Join-Path $LogDir 'rebuild.log'
$TimeoutWrapper = Join-Path $RepoRoot 'tools\shell\run_with_timeout.ps1'
$GraphOut = Join-Path $RepoRoot 'graphify-out'
$GraphStateNames = @('graph.json', 'manifest.json', '.graphify_labels.json', 'GRAPH_REPORT.md')
$GraphBackupDir = Join-Path ([IO.Path]::GetTempPath()) ("hft3-graphify-backup-{0}" -f ([guid]::NewGuid()))
$UpdateTimeoutSec = 420
$ClusterTimeoutSec = 420
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LogLine {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $Message"
    $line | Tee-Object -FilePath $LogFile -Append
    return $line
}

function Backup-GraphState {
    New-Item -ItemType Directory -Force -Path $GraphBackupDir | Out-Null
    foreach ($name in $GraphStateNames) {
        $source = Join-Path $GraphOut $name
        if (Test-Path $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $GraphBackupDir $name) -Force
        }
    }
}

function Clear-GraphState {
    foreach ($name in $GraphStateNames) {
        $path = Join-Path $GraphOut $name
        if (Test-Path $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}

function Restore-GraphState {
    if (-not (Test-Path $GraphBackupDir)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $GraphOut | Out-Null
    foreach ($name in $GraphStateNames) {
        $backup = Join-Path $GraphBackupDir $name
        if (Test-Path $backup) {
            Copy-Item -LiteralPath $backup -Destination (Join-Path $GraphOut $name) -Force
        }
    }
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

$updateExit = 0
$clusterExit = 0
try {
    Backup-GraphState
    Clear-GraphState
    Write-LogLine "graphify update start (timeout ${UpdateTimeoutSec}s, force, no-cluster)" | Out-Null
    & $TimeoutWrapper -TimeoutSec $UpdateTimeoutSec -Label 'graphify-update' -- python -m graphify update . --force --no-cluster 2>&1 |
        Tee-Object -FilePath $LogFile -Append
    $updateExit = $LASTEXITCODE
    if ($updateExit -ne 0) {
        throw "graphify update . --force --no-cluster exited $updateExit"
    }

    Write-LogLine "graphify cluster-only start (timeout ${ClusterTimeoutSec}s, no-viz)" | Out-Null
    & $TimeoutWrapper -TimeoutSec $ClusterTimeoutSec -Label 'graphify-cluster' -- python -m graphify cluster-only . --no-viz 2>&1 |
        Tee-Object -FilePath $LogFile -Append
    $clusterExit = $LASTEXITCODE
    if ($clusterExit -ne 0) {
        throw "graphify cluster-only . --no-viz exited $clusterExit"
    }

    Write-LogLine 'graphify rebuild done (update --force --no-cluster; cluster-only --no-viz)' | Out-Null
    Remove-Item -LiteralPath $GraphBackupDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
} catch {
    $updateMessage = $_.Exception.Message
    $exitCode = if ($clusterExit -ne 0) { $clusterExit } elseif ($updateExit -ne 0) { $updateExit } else { 1 }
    Restore-GraphState
    Remove-Item -LiteralPath $GraphBackupDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-LogLine "graphify rebuild failed (exit $exitCode): $updateMessage" | Out-Null
    exit $exitCode
}
