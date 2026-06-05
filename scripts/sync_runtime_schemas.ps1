# Sync packet JSON schemas to runtime/schemas (draft-07 mirror).
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$src = Join-Path $RepoRoot "packages\data_layer\packet"
$dest = Join-Path $RepoRoot "runtime\schemas"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$files = @(
    "schema_v1.json",
    "schema_aar_response_v1.json",
    "schema_pipeline_request_v1.json",
    "schema_pipeline_response_v1.json",
    "schema_pipeline_hypothesis_response_v1.json",
    "schema_pipeline_idea_set_v1.json",
    "schema_research_decision_packet_v1.json",
    "schema_mbo_feature_packet_v1.json"
)

$drift = $false
foreach ($name in $files) {
    $from = Join-Path $src $name
    $to = Join-Path $dest $name
    if (-not (Test-Path $from)) {
        Write-Error "Missing source schema: $from"
        exit 1
    }
    Copy-Item -Force $from $to
    Write-Host "Synced $name -> runtime/schemas/"
}

Write-Host "Schema sync complete."
