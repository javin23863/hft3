# Orchestrate crypto gap-fill for full-year 2024 production testing.

param(

    [switch]$DryRun,

    [switch]$SyncChi404Node,

    [switch]$SkipChi404,

    [double]$WsRttMs,

    [switch]$ForceReplaceSynthetic,

    [switch]$AllowDegraded,

    [switch]$ContinueOnError,

    [switch]$RefreshB2Probe

)



$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

Set-Location $RepoRoot

$env:PYTHONPATH = "packages"



$argsList = @("-m", "crypto_lane.pipeline", "fill-test-gaps")

if ($DryRun) { $argsList += "--dry-run" }

if ($SyncChi404Node) { $argsList += "--sync-chi404-node" }

if ($SkipChi404) { $argsList += "--skip-chi404" }

if ($PSBoundParameters.ContainsKey("WsRttMs")) { $argsList += @("--ws-rtt-ms", "$WsRttMs") }

if ($ForceReplaceSynthetic) { $argsList += "--force-replace-synthetic" }

if ($AllowDegraded) { $argsList += "--allow-degraded" }

if ($ContinueOnError) { $argsList += "--continue-on-error" }
if ($RefreshB2Probe) { $argsList += "--refresh-b2-probe" }



python @argsList

exit $LASTEXITCODE

