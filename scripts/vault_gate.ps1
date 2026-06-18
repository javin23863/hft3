# Blocking Obsidian vault ontology gate — run BEFORE any code locate/edit session.
# Writes runtime/vault-gate/.last-vault-gate.json (proof of consult for this session).
param(
    [Parameter(Mandatory = $true)]
    [string]$Query,
    [string]$Purpose = "code-edit"
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VaultRoot = if ($env:HFT3_VAULT_ROOT) { $env:HFT3_VAULT_ROOT } else { 'C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3' }
$StampDir = Join-Path $RepoRoot 'runtime\vault-gate'
$Stamp = Join-Path $StampDir '.last-vault-gate.json'

$Required = @(
    (Join-Path $VaultRoot 'wiki\hot.md'),
    (Join-Path $VaultRoot 'Home.md'),
    (Join-Path $VaultRoot 'Memory Stack.md')
)

foreach ($path in $Required) {
    if (-not (Test-Path $path)) {
        Write-Error "VaultGate required file missing: $path"
    }
}

Write-Host "VAULT GATE vault: $VaultRoot"
Write-Host "VAULT GATE query: $Query"

$hotExcerpt = (Get-Content (Join-Path $VaultRoot 'wiki\hot.md') -TotalCount 40) -join "`n"
$memoryExcerpt = (Get-Content (Join-Path $VaultRoot 'Memory Stack.md') -TotalCount 30) -join "`n"

$searchHits = @()
if (Get-Command rg -ErrorAction SilentlyContinue) {
    $rgOut = & rg -n -i --max-count 8 $Query $VaultRoot -g '*.md' 2>&1
    if ($LASTEXITCODE -le 1) {
        $searchHits = @($rgOut | ForEach-Object { "$_" })
    }
} else {
    Write-Warning 'rg not on PATH; vault keyword search skipped'
}

$graphWaiver = $hotExcerpt -match 'Temporary graph waiver|waived-by-owner-2026-06-16'

$payload = @{
    timestamp_utc   = (Get-Date).ToUniversalTime().ToString('o')
    purpose         = $Purpose
    query           = $Query
    vault_root      = $VaultRoot
    required_reads  = @('wiki/hot.md', 'Home.md', 'Memory Stack.md')
    hot_excerpt     = $hotExcerpt.Substring(0, [Math]::Min(3000, $hotExcerpt.Length))
    memory_excerpt  = $memoryExcerpt.Substring(0, [Math]::Min(2000, $memoryExcerpt.Length))
    search_hits     = $searchHits
    graph_gates     = if ($graphWaiver) { 'waived-by-owner-2026-06-16' } else { 'active' }
    repo_root       = $RepoRoot
} | ConvertTo-Json -Depth 6

New-Item -ItemType Directory -Force -Path $StampDir | Out-Null
Set-Content -Path $Stamp -Value $payload -Encoding UTF8
Write-Host "Wrote $Stamp"
if ($graphWaiver) {
    Write-Host 'NOTE: graph gates waived-by-owner-2026-06-16 (vault hot.md). Use VaultGate + source reads; do not require GraphGate.'
}
Write-Host 'OK: vault ontology consult recorded. Proceed only from vault + canonical repo docs.'
