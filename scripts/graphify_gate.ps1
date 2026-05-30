# Blocking graph consult gate — run BEFORE any code locate/edit session.
# Writes graphify-out/.last-graph-query.json (proof of consult for this session).
param(
    [Parameter(Mandatory = $true)]
    [string]$Query,
    [string]$Purpose = "code-edit"
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$GraphJson = Join-Path $RepoRoot 'graphify-out\graph.json'
$Stamp = Join-Path $RepoRoot 'graphify-out\.last-graph-query.json'

if (-not (Get-Command graphify -ErrorAction SilentlyContinue)) {
    Write-Error 'graphify not on PATH. pip install graphifyy; see docs/GRAPHIFY_WORKFLOW.md'
}

if (-not (Test-Path $GraphJson)) {
    Write-Host 'graphify-out/graph.json missing — running graphify update . (AST-only)...'
    & graphify update .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "GRAPH GATE query: $Query"
$outLines = & graphify query $Query 2>&1
$queryExit = $LASTEXITCODE
$out = ($outLines | Out-String).Trim()
Write-Host $out
if ($queryExit -ne 0) {
    Write-Error "graphify query failed (exit $queryExit)"
}
if ($out.Length -lt 40) {
    Write-Error "graphify query output too short (${out.Length} chars) — likely empty or failed"
}

$payload = @{
    timestamp_utc = (Get-Date).ToUniversalTime().ToString('o')
    purpose       = $Purpose
    query         = $Query
    output_excerpt = $out.Substring(0, [Math]::Min(4000, $out.Length))
    repo_root     = $RepoRoot
} | ConvertTo-Json -Depth 4

New-Item -ItemType Directory -Force -Path (Split-Path $Stamp) | Out-Null
Set-Content -Path $Stamp -Value $payload -Encoding UTF8
Write-Host "Wrote $Stamp"
Write-Host 'OK: graph consult recorded. Proceed to Plan/Locate using graph context — not blind repo grep.'
