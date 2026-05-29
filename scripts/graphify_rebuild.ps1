# Rebuild graphify graph from repo root after code edits.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\graphify'
$LogFile = Join-Path $LogDir 'rebuild.log'
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

$updated = $false
try {
    & graphify update . 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -eq 0) {
        $updated = $true
    } else {
        throw "graphify update . exited $LASTEXITCODE"
    }
} catch {
    Write-LogLine "fallback: graphify cluster-only . ($($_.Exception.Message))" | Out-Null
    & graphify cluster-only . 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) {
        Write-LogLine "graphify rebuild failed (exit $LASTEXITCODE)" | Out-Null
        exit $LASTEXITCODE
    }
}

if ($updated) {
    Write-LogLine 'graphify rebuild done (update)' | Out-Null
} else {
    Write-LogLine 'graphify rebuild done (cluster-only)' | Out-Null
}
exit 0
