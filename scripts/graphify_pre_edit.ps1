# Ensure graph exists; orchestrator MUST run graphify_gate.ps1 with a query before edits.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$GraphJson = Join-Path $RepoRoot 'graphify-out\graph.json'
$Report = Join-Path $RepoRoot 'graphify-out\GRAPH_REPORT.md'
$Stamp = Join-Path $RepoRoot 'graphify-out\.last-graph-query.json'

if (-not (Get-Command graphify -ErrorAction SilentlyContinue)) {
    Write-Error 'graphify not on PATH. pip install graphifyy; see docs/GRAPHIFY_WORKFLOW.md'
}

if (-not (Test-Path $GraphJson)) {
    Write-Host 'graphify-out/graph.json missing - running graphify update . (AST-only)...'
    & graphify update .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} elseif (Test-Path $Report) {
    $ageHours = ((Get-Date) - (Get-Item $Report).LastWriteTime).TotalHours
    if ($ageHours -gt 24) {
        Write-Host ("GRAPH_REPORT.md is {0:0.0}h old - consider graphify query or rebuild." -f $ageHours)
    }
}

Write-Host ''
Write-Host 'BLOCKING: Before any code edit you MUST run graphify_gate with a task-specific query:'
Write-Host '  .\scripts\graphify_gate.ps1 -Query "where is X and what calls it"'
Write-Host 'CHI404/R|Trader work: read docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md after graph query.'
Write-Host 'After edits: .\scripts\graphify_rebuild.ps1'

if (-not (Test-Path $Stamp)) {
    Write-Host ''
    Write-Warning '.last-graph-query.json missing - graph gate not run for this session.'
    exit 2
}

try {
    $stampRaw = Get-Content $Stamp -Raw
    $stampJson = $stampRaw | ConvertFrom-Json
    $timestampMatch = [regex]::Match($stampRaw, '"timestamp_utc"\s*:\s*"([^"]+)"')
    if (-not $timestampMatch.Success) {
        Write-Warning 'Graph gate stamp missing timestamp_utc - re-run graphify_gate.ps1.'
        exit 2
    }
    $stampUtc = [System.DateTimeOffset]::Parse(
        $timestampMatch.Groups[1].Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
    $ageMin = ((Get-Date).ToUniversalTime() - $stampUtc).TotalMinutes
    $excerpt = [string]$stampJson.output_excerpt
    if ($excerpt.Length -lt 40) {
        Write-Warning 'Graph gate stamp has empty/short output_excerpt - re-run graphify_gate.ps1.'
        exit 2
    }
} catch {
    Write-Warning "Graph gate stamp invalid JSON: $_"
    exit 2
}

if ($ageMin -gt 240) {
    Write-Warning ("Graph gate stamp is {0:0.0}m old - re-run graphify_gate.ps1 before editing." -f $ageMin)
    exit 2
}

Write-Host ("OK: graph gate stamp fresh ({0:0.0}m)" -f $ageMin)
