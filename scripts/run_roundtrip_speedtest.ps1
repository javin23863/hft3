# Run round-trip latency probe from repo root.
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\roundtrip_speedtest'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$pyArgs = @('scripts/roundtrip_speedtest.py') + $args
& python @pyArgs
exit $LASTEXITCODE
