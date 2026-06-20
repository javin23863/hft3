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

function Sanitize-RemoteBash([string]$cmd) {
    return ($cmd -replace "`r", "").Trim()
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

$localHead = (git rev-parse HEAD).Trim()
$sshOpts = @("-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-p", "$SshPort")
$scpOpts = @("-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new", "-P", "$SshPort")

Write-Step "Push branch $GitBranch to origin"
if (-not $SkipPush) {
    & git push -u origin $GitBranch
    if ($LASTEXITCODE -ne 0) { throw "git push failed exit=$LASTEXITCODE" }
}

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
& ssh @sshOpts $SshHost (Sanitize-RemoteBash $syncCmd)
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

Write-Step "Verify remote HEAD + hashes"
$verifyCmd = @'
set -euo pipefail
export DEPLOY_REPO="__REMOTE_REPO__"
export DEPLOY_EVENTS="__REMOTE_REPO__/__EVENTS_CSV__"
export DEPLOY_MANIFEST="__REMOTE_MANIFEST__"
export DEPLOY_HEAD="__LOCAL_HEAD__"
cd "$DEPLOY_REPO"
python3 - <<'PY'
import hashlib, json, os, subprocess, sys
from pathlib import Path

repo = Path(os.environ["DEPLOY_REPO"])
events = Path(os.environ["DEPLOY_EVENTS"])
manifest = Path(os.environ["DEPLOY_MANIFEST"])
expected_head = os.environ["DEPLOY_HEAD"]
gate = json.loads((repo / "runtime/reports/paid_screen_ready_gate.json").read_text(encoding="utf-8"))
expected_events = gate["pilot_hashes"]["events_csv_hash"]
expected_lake = gate["pilot_hashes"]["lake_manifest_hash"]

def sha32(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:32]

head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
events_h = sha32(events)
lake_h = sha32(manifest)
print(f"REMOTE_HEAD={head}")
print(f"EVENTS_HASH={events_h}")
print(f"LAKE_HASH={lake_h}")
if head != expected_head:
    print(f"FAIL: HEAD {head} != expected {expected_head}", file=sys.stderr)
    sys.exit(1)
if events_h != expected_events:
    print(f"FAIL: events hash {events_h} != gate {expected_events}", file=sys.stderr)
    sys.exit(1)
if lake_h != expected_lake:
    print(f"FAIL: lake hash {lake_h} != gate {expected_lake}", file=sys.stderr)
    sys.exit(1)
print("HASH_VERIFY_OK")
PY
'@ -replace '__REMOTE_REPO__', $RemoteRepo -replace '__EVENTS_CSV__', ($EventsCsv -replace '\\','/') -replace '__REMOTE_MANIFEST__', $RemoteManifestPath -replace '__LOCAL_HEAD__', $localHead
& ssh @sshOpts $SshHost (Sanitize-RemoteBash $verifyCmd)
if ($LASTEXITCODE -ne 0) { throw "Remote hash verify failed exit=$LASTEXITCODE" }

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

Write-Step "20-unit NPZ resolution probe"
$probeCmd = @'
set -euo pipefail
cd __REMOTE_REPO__
export HFT3_NPZ_ROOT=__REMOTE_NPZ__
export HFT3_MANIFEST_PATH=__REMOTE_MANIFEST__
python3 - <<'PY'
import json, random, sys
from pathlib import Path

repo = Path("__REMOTE_REPO__")
sys.path.insert(0, str(repo))
from hft3_bootstrap import setup_repo_paths
setup_repo_paths()
from backtest_pipeline.src.vectorbt_adapter import _npz_candidates_for_event
from data_system.src.event_data_resolver import npz_search_dirs

smoke = repo / "runtime/reports/vbt_smoke_units.jsonl"
units_path = smoke if smoke.is_file() else repo / "runtime/reports/vbt_full_units.jsonl"
if not units_path.is_file():
    print("FAIL: no probe units JSONL", file=sys.stderr)
    sys.exit(1)
rows = [json.loads(l) for l in units_path.read_text(encoding="utf-8").splitlines() if l.strip()]
probe_n = __PROBE_N__
sample = rows[:probe_n] if len(rows) <= probe_n else random.sample(rows, probe_n)
hits = 0
for u in sample:
    cands = _npz_candidates_for_event(npz_search_dirs(repo), u["event_id"], u.get("symbol"))
    if cands:
        hits += 1
print(f"PROBE_HITS={hits}/{len(sample)}")
if hits != len(sample):
    print("FAIL: NPZ resolution probe", file=sys.stderr)
    sys.exit(1)
PY
'@ -replace '__REMOTE_REPO__', $RemoteRepo -replace '__REMOTE_NPZ__', $RemoteNpzRoot -replace '__REMOTE_MANIFEST__', $RemoteManifestPath -replace '__PROBE_N__', "$ProbeUnitCount"
& ssh @sshOpts $SshHost (Sanitize-RemoteBash $probeCmd)
if ($LASTEXITCODE -ne 0) { throw "NPZ resolution probe failed exit=$LASTEXITCODE" }

Write-Host "`nDEPLOY_CONTRACT_PASS" -ForegroundColor Green
Write-Host "branch=$GitBranch head=$localHead ssh=${SshHost}:${SshPort} repo=$RemoteRepo"
