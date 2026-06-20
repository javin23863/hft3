# Sole supported Vast deploy path for VectorBT paid screen (Plan v3 Phase B).
# Authority: docs/project/VBT_PAID_SCREEN_RUNBOOK.md, docs/ai/PONYTAIL.md (manifest.parquet hash)
# Outputs DEPLOY_CONTRACT_PASS on success; exit 1 otherwise.
param(
    [string]$SshHost = $(if ($env:VAST_SSH_HOST) { $env:VAST_SSH_HOST } else { "root@ssh7.vast.ai" }),
    [int]$SshPort = $(if ($env:VAST_SSH_PORT) { [int]$env:VAST_SSH_PORT } else { 15808 }),
    [string]$RemoteRepo = $(if ($env:VAST_REMOTE_REPO) { $env:VAST_REMOTE_REPO } else { "/root/hft3/repo" }),
    [string]$GitBranch = $(if ($env:HFT3_VAST_GIT_BRANCH) { $env:HFT3_VAST_GIT_BRANCH } else { "cursor/vast-vbt-workflow" }),
    [string]$RepoRoot = $(if ($env:HFT3_REPO) { $env:HFT3_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }),
    [string]$GateFile = "runtime/reports/paid_screen_ready_gate.json",
    [string]$EventsCsv = "packages/data_system/config/events.csv",
    [string]$ManifestParquet = $(if ($env:HFT3_MANIFEST_PATH) { $env:HFT3_MANIFEST_PATH } elseif (Test-Path "C:\hft3-lake\manifest.parquet") { "C:\hft3-lake\manifest.parquet" } else { "" }),
    [string]$LocalNpzRoot = $(if ($env:HFT3_NPZ_ROOT) { $env:HFT3_NPZ_ROOT } elseif (Test-Path "C:\hft3-lake\npz") { "C:\hft3-lake\npz" } else { "" }),
    [string]$RemoteNpzRoot = "/data/npz",
    [string]$RemoteManifestPath = "/data/npz/manifest.parquet",
    [int]$ProbeUnitCount = 20,
    [switch]$SkipPush
)

$ErrorActionPreference = "Stop"

function Send-RemoteBash([string]$script) {
    ($script -replace "`r", "") | & ssh @sshOpts $SshHost bash
}

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

function Get-FileSha256Prefix([string]$Path) {
    $hash = Get-FileHash -Algorithm SHA256 -Path $Path
    return $hash.Hash.Substring(0, 32).ToLower()
}

Set-Location $RepoRoot
$gatePath = Join-Path $RepoRoot $GateFile
$eventsPath = Join-Path $RepoRoot $EventsCsv

if (-not (Test-Path $gatePath)) { throw "Gate file missing: $gatePath" }
if (-not (Test-Path $eventsPath)) { throw "events.csv missing: $eventsPath" }
if (-not $ManifestParquet -or -not (Test-Path $ManifestParquet)) {
    throw "manifest.parquet missing (set HFT3_MANIFEST_PATH or place at C:\hft3-lake\manifest.parquet)"
}

$gate = Get-Content $gatePath -Raw | ConvertFrom-Json
if (-not $gate.ready_for_full_run) { throw "Gate ready_for_full_run is not true" }

$expectedEventsHash = $gate.pilot_hashes.events_csv_hash
$expectedLakeHash = $gate.pilot_hashes.lake_manifest_hash
$localEventsHash = Get-FileSha256Prefix $eventsPath
$localLakeHash = Get-FileSha256Prefix $ManifestParquet

if ($localEventsHash -ne $expectedEventsHash) {
    throw "Local events.csv hash $localEventsHash != gate $expectedEventsHash"
}
if ($localLakeHash -ne $expectedLakeHash) {
    throw "Local manifest.parquet hash $localLakeHash != gate $expectedLakeHash"
}

if ($localLakeHash -ne $expectedLakeHash) {
    throw "Local manifest.parquet hash $localLakeHash != gate $expectedLakeHash"
}

$sshOpts = @("-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-p", "$SshPort")
$scpOpts = @("-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-P", "$SshPort")

Write-Step "Push branch $GitBranch to origin"
if (-not $SkipPush) {
    & git push -u origin $GitBranch
    if ($LASTEXITCODE -ne 0) { throw "git push failed exit=$LASTEXITCODE" }
}
& git fetch origin $GitBranch 2>&1 | Out-Null
$localHead = (git rev-parse "origin/$GitBranch").Trim()

Write-Step "Remote repo sync"
$syncCmd = @"
set -euo pipefail
if [[ -d $RemoteRepo/.git ]]; then
  git -C $RemoteRepo fetch origin
  git -C $RemoteRepo checkout $GitBranch
  git -C $RemoteRepo reset --hard origin/$GitBranch
else
  git clone --branch $GitBranch https://github.com/javin23863/hft3.git $RemoteRepo
fi
cd $RemoteRepo
echo REMOTE_HEAD=\$(git rev-parse HEAD)
"@
Send-RemoteBash $syncCmd
if ($LASTEXITCODE -ne 0) { throw "Remote sync failed exit=$LASTEXITCODE" }

Write-Step "SCP gate, events.csv, manifest.parquet"
$remoteGate = "$RemoteRepo/runtime/reports/paid_screen_ready_gate.json"
$remoteEvents = "$RemoteRepo/$EventsCsv"
& ssh @sshOpts $SshHost "mkdir -p $(Split-Path $remoteEvents -Parent) $RemoteRepo/runtime/reports $(Split-Path $RemoteManifestPath -Parent)"
& scp @scpOpts $gatePath "${SshHost}:${remoteGate}"
if ($LASTEXITCODE -ne 0) { throw "scp gate failed exit=$LASTEXITCODE" }
& scp @scpOpts $eventsPath "${SshHost}:${remoteEvents}"
if ($LASTEXITCODE -ne 0) { throw "scp events failed exit=$LASTEXITCODE" }
& scp @scpOpts $ManifestParquet "${SshHost}:${RemoteManifestPath}"
if ($LASTEXITCODE -ne 0) { throw "scp manifest failed exit=$LASTEXITCODE" }
$smokeUnits = Join-Path $RepoRoot "runtime/reports/vbt_smoke_units.jsonl"
if (Test-Path $smokeUnits) {
    & scp @scpOpts $smokeUnits "${SshHost}:${RemoteRepo}/runtime/reports/vbt_smoke_units.jsonl"
    if ($LASTEXITCODE -ne 0) { throw "scp smoke units failed exit=$LASTEXITCODE" }
}

Write-Step "Verify remote HEAD + hashes + NPZ probe"
$remoteVerify = @"
export DEPLOY_REPO='$RemoteRepo'
export DEPLOY_EVENTS='$RemoteRepo/$($EventsCsv -replace '\\','/')'
export DEPLOY_MANIFEST='$RemoteManifestPath'
export DEPLOY_HEAD='$localHead'
export DEPLOY_NPZ_ROOT='$RemoteNpzRoot'
export DEPLOY_PROBE_N='$ProbeUnitCount'
bash $RemoteRepo/scripts/vast_remote_verify.sh
"@
Send-RemoteBash $remoteVerify
if ($LASTEXITCODE -ne 0) { throw "Remote verify failed exit=$LASTEXITCODE" }

Write-Step "NPZ parity probe (file counts)"
$localNpzCount = 0
if ($LocalNpzRoot -and (Test-Path $LocalNpzRoot)) {
    $localNpzCount = (Get-ChildItem $LocalNpzRoot -Filter *.npz -ErrorAction SilentlyContinue | Measure-Object).Count
}
$remoteNpzCmd = "find $RemoteNpzRoot -maxdepth 1 -type f -name '*.npz' 2>/dev/null | wc -l"
$remoteNpzCount = (& ssh @sshOpts $SshHost $remoteNpzCmd).Trim()
Write-Host "local_npz=$localNpzCount remote_npz=$remoteNpzCount"
if ([int]$localNpzCount -gt 0 -and [int]$remoteNpzCount -lt 1) {
    throw "Remote NPZ root empty but local has $localNpzCount files"
}
if ([int]$localNpzCount -gt 0) {
    $ratio = [double]$remoteNpzCount / [double]$localNpzCount
    if ($ratio -lt 0.95) {
        throw "NPZ parity fail: remote/local ratio $ratio (< 0.95)"
    }
}

Write-Host "`nDEPLOY_CONTRACT_PASS" -ForegroundColor Green
Write-Host "branch=$GitBranch head=$localHead ssh=${SshHost}:${SshPort} repo=$RemoteRepo"
