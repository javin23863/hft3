# Ensure graph exists; remind orchestrator to query graph before edits.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$GraphJson = Join-Path $RepoRoot 'graphify-out\graph.json'
$Report = Join-Path $RepoRoot 'graphify-out\GRAPH_REPORT.md'

if (-not (Get-Command graphify -ErrorAction SilentlyContinue)) {
    Write-Warning 'graphify not on PATH. pip install graphifyy; see docs/GRAPHIFY_WORKFLOW.md'
    exit 1
}

if (-not (Test-Path $GraphJson)) {
    Write-Host 'graphify-out/graph.json missing — running AST build (graphify update .)...'
    Write-Host 'Optional: full semantic rebuild uses local Ollama — .\scripts\graphify_semantic_local.ps1 (not Google API).'
    & graphify update . 2>&1
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} elseif (Test-Path $Report) {
    $ageHours = ((Get-Date) - (Get-Item $Report).LastWriteTime).TotalHours
    if ($ageHours -gt 24) {
        Write-Host ("GRAPH_REPORT.md is {0:0.0}h old — consider graphify query or rebuild." -f $ageHours)
    }
}

Write-Host ''
Write-Host 'REMINDER: Before code edits, consult the graph:'
Write-Host '  graphify query "<natural language question>"'
Write-Host '  or read graphify-out/GRAPH_REPORT.md if fresh'
Write-Host 'After edits: .\scripts\graphify_rebuild.ps1'
