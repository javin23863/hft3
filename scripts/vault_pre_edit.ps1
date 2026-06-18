# Ensure vault ontology gate ran before edits. Orchestrator MUST run vault_gate.ps1 first.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Stamp = Join-Path $RepoRoot 'runtime\vault-gate\.last-vault-gate.json'

Write-Host ''
Write-Host 'BLOCKING: Before any code edit you MUST run VaultGate with a task-specific query:'
Write-Host '  .\scripts\vault_gate.ps1 -Query "HftBacktest campaign handoff constraints"'
Write-Host 'Vault path: $env:HFT3_VAULT_ROOT or C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3'
Write-Host 'Read wiki/hot.md, Home.md, Memory Stack.md + relevant decisions/ every session.'

if (-not (Test-Path $Stamp)) {
    Write-Host ''
    Write-Warning '.last-vault-gate.json missing - VaultGate not run for this session.'
    exit 2
}

try {
    $stampRaw = Get-Content $Stamp -Raw
    $stampJson = $stampRaw | ConvertFrom-Json
    $timestampMatch = [regex]::Match($stampRaw, '"timestamp_utc"\s*:\s*"([^"]+)"')
    if (-not $timestampMatch.Success) {
        Write-Warning 'Vault gate stamp missing timestamp_utc - re-run vault_gate.ps1.'
        exit 2
    }
    $stampUtc = [System.DateTimeOffset]::Parse(
        $timestampMatch.Groups[1].Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
    $ageMin = ((Get-Date).ToUniversalTime() - $stampUtc).TotalMinutes
    $hot = [string]$stampJson.hot_excerpt
    if ($hot.Length -lt 40) {
        Write-Warning 'Vault gate stamp has empty/short hot_excerpt - re-run vault_gate.ps1.'
        exit 2
    }
} catch {
    Write-Warning "Vault gate stamp invalid JSON: $_"
    exit 2
}

if ($ageMin -gt 240) {
    Write-Warning ("Vault gate stamp is {0:0.0}m old - re-run vault_gate.ps1 before editing." -f $ageMin)
    exit 2
}

Write-Host ("OK: vault gate stamp fresh ({0:0.0}m); graph_gates={1}" -f $ageMin, $stampJson.graph_gates)
