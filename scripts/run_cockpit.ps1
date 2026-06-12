# run_cockpit.ps1 — launch the HFT3 Cockpit backend.
# Read-only aggregation service. Control plane is local-origin only.
#
# Usage:
#   scripts\run_cockpit.ps1                 # loopback dev (no token)
#   $env:COCKPIT_VIEW_TOKEN="secret"; scripts\run_cockpit.ps1 -BindAll
param(
    [int]$Port = 8080,
    [switch]$BindAll  # bind 0.0.0.0 for LAN/remote (put Caddy in front for TLS)
)

$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$repo;$repo/packages"

$bind = if ($BindAll) { "0.0.0.0" } else { "127.0.0.1" }

if ($BindAll -and -not $env:COCKPIT_VIEW_TOKEN) {
    Write-Warning "Binding $bind with no COCKPIT_VIEW_TOKEN set — remote reads will be refused (loopback-only)."
}

Write-Host "HFT3 Cockpit -> http://${bind}:$Port  (control plane: local-origin only)"
python -m uvicorn apps.cockpit.backend.main:app --host $bind --port $Port
