# run_cockpit.ps1 - launch the HFT3 Cockpit backend.
# Read-only aggregation service. Control plane is local-origin only.
#
# Usage:
#   scripts\run_cockpit.ps1                 # loopback dev (no token)
#   $env:COCKPIT_VIEW_TOKEN="secret"; scripts\run_cockpit.ps1 -BindAll
param(
    [int]$Port = 8080,
    [switch]$BindAll,  # bind 0.0.0.0 for LAN/remote (put Caddy in front for TLS)
    [switch]$EnableControlExec
)

$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$repo;$repo/packages"

$bind = if ($BindAll) { "0.0.0.0" } else { "127.0.0.1" }

if ($EnableControlExec) {
    $env:COCKPIT_CONTROL_EXEC = "1"
}

if ($BindAll -and -not $env:COCKPIT_VIEW_TOKEN) {
    Write-Warning "Binding $bind with no COCKPIT_VIEW_TOKEN set - remote reads will be refused (loopback-only)."
}

$execState = if ($env:COCKPIT_CONTROL_EXEC -eq "1") { "true" } else { "false" }
$url = "http://{0}:{1}" -f $bind, $Port
Write-Host ("HFT3 Cockpit -> {0}" -f $url)
Write-Host ("control plane: local-origin only; exec={0}" -f $execState)
python -m uvicorn apps.cockpit.backend.main:app --host $bind --port $Port
