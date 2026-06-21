# Sole supported Vast deploy path for VectorBT paid screen (Plan v3 Phase B).
# Authority: docs/project/VBT_PAID_SCREEN_RUNBOOK.md, docs/ai/PONYTAIL.md (manifest.parquet hash)
# Outputs DEPLOY_CONTRACT_PASS on success; exit 1 otherwise.
param(
    [string]$SshHost = $(if ($env:VAST_SSH_HOST) { $env:VAST_SSH_HOST } else { "root@ssh7.vast.ai" }),
    [int]$SshPort = $(if ($env:VAST_SSH_PORT) { [int]$env:VAST_SSH_PORT } else { 15808 }),
    [string]$RemoteRepo = $(if ($env:VAST_REMOTE_REPO) { $env:VAST_REMOTE_REPO } else { "/root/hft3/repo" }),
    [string]$GitBranch = $(if ($env:HFT3_VAST_GIT_BRANCH) { $env:HFT3_VAST_GIT_BRANCH } else { "" }),
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

function ConvertTo-BashSingleQuotedArg([string]$Value) {
    if ($null -eq $Value) { throw "Cannot shell-quote null value" }
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Invoke-RemoteBash([string]$script, [string[]]$Arguments = @()) {
    $remoteCommand = "bash -s"
    if ($Arguments.Count -gt 0) {
        $quotedArgs = ($Arguments | ForEach-Object { ConvertTo-BashSingleQuotedArg $_ }) -join " "
        $remoteCommand = "bash -s -- $quotedArgs"
    }
    ($script -replace "`r", "") | & ssh @sshOpts $SshHost $remoteCommand
}

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

function Get-FileSha256Prefix([string]$Path) {
    $hash = Get-FileHash -Algorithm SHA256 -Path $Path
    return $hash.Hash.Substring(0, 32).ToLower()
}

function Assert-ValidGitBranch([string]$Branch) {
    if (-not $Branch -or $Branch.StartsWith("-")) {
        throw "Invalid GitBranch: $Branch"
    }
    & git check-ref-format --branch $Branch *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Invalid GitBranch: $Branch"
    }
}

function Normalize-RemoteAbsolutePath([string]$Name, [string]$Path) {
    if (-not $Path) { throw "$Name is empty" }
    $normalized = $Path -replace "\\", "/"
    if (-not $normalized.StartsWith("/")) {
        throw "$Name must be an absolute Unix path: $Path"
    }
    if ($normalized -notmatch '^[A-Za-z0-9_./-]+$' -or $normalized -match '(^|/)\.\.(/|$)') {
        throw "$Name contains unsafe path characters: $Path"
    }
    if ($normalized.Length -gt 1) {
        $normalized = $normalized.TrimEnd("/")
    }
    return $normalized
}

function Normalize-RepoRelativePath([string]$Name, [string]$Path) {
    if (-not $Path) { throw "$Name is empty" }
    $normalized = $Path -replace "\\", "/"
    if ($normalized.StartsWith("/") -or $normalized -match '^[A-Za-z]:') {
        throw "$Name must be repo-relative: $Path"
    }
    if ($normalized -notmatch '^[A-Za-z0-9_./-]+$' -or $normalized -match '(^|/)\.\.(/|$)') {
        throw "$Name contains unsafe path characters: $Path"
    }
    return $normalized.TrimStart("./")
}

function Get-RemotePosixParent([string]$Name, [string]$Path) {
    if (-not $Path -or -not $Path.StartsWith("/")) {
        throw "$Name must be an absolute Unix path: $Path"
    }
    $trimmed = $Path.TrimEnd("/")
    $lastSlash = $trimmed.LastIndexOf("/")
    if ($lastSlash -le 0) {
        return "/"
    }
    return $trimmed.Substring(0, $lastSlash)
}

Set-Location $RepoRoot
if (-not $GitBranch) {
    $GitBranch = (git branch --show-current).Trim()
    if (-not $GitBranch) {
        throw "GitBranch not provided and current checkout is detached; set HFT3_VAST_GIT_BRANCH"
    }
}
Assert-ValidGitBranch $GitBranch
$RemoteRepo = Normalize-RemoteAbsolutePath "RemoteRepo" $RemoteRepo
$RemoteNpzRoot = Normalize-RemoteAbsolutePath "RemoteNpzRoot" $RemoteNpzRoot
$RemoteManifestPath = Normalize-RemoteAbsolutePath "RemoteManifestPath" $RemoteManifestPath
$EventsCsvRemote = Normalize-RepoRelativePath "EventsCsv" $EventsCsv
$gatePath = Join-Path $RepoRoot $GateFile
$eventsPath = Join-Path $RepoRoot $EventsCsv
$declPath = Join-Path $RepoRoot "runtime/reports/vbt_full_run_declaration.json"

if (-not (Test-Path $gatePath)) { throw "Gate file missing: $gatePath" }
if (-not (Test-Path $eventsPath)) { throw "events.csv missing: $eventsPath" }
if (-not (Test-Path $declPath)) { throw "Full-run declaration missing: $declPath" }
if (-not $ManifestParquet -or -not (Test-Path $ManifestParquet)) {
    throw "manifest.parquet missing (set HFT3_MANIFEST_PATH or place at C:\hft3-lake\manifest.parquet)"
}

$gate = Get-Content $gatePath -Raw | ConvertFrom-Json
$decl = Get-Content $declPath -Raw | ConvertFrom-Json
if (-not $gate.ready_for_full_run) { throw "Gate ready_for_full_run is not true" }

$expectedEventsHash = $gate.pilot_hashes.events_csv_hash
$expectedLakeHash = $gate.pilot_hashes.lake_manifest_hash
$localEventsHash = Get-FileSha256Prefix $eventsPath
$localLakeHash = Get-FileSha256Prefix $ManifestParquet
$declHead = [string]$decl.git_head
$declEventsHash = [string]$decl.events_csv_hash
$declLakeHash = [string]$decl.lake_manifest_hash
$repoHead = (git rev-parse HEAD).Trim()

if ($localEventsHash -ne $expectedEventsHash) {
    throw "Local events.csv hash $localEventsHash != gate $expectedEventsHash"
}
if ($localLakeHash -ne $expectedLakeHash) {
    throw "Local manifest.parquet hash $localLakeHash != gate $expectedLakeHash"
}
if (-not $declHead) {
    throw "Declaration git_head missing in $declPath"
}
if ($repoHead -ne $declHead) {
    throw "Local HEAD $repoHead != declaration git_head $declHead"
}
if ($declEventsHash -ne $expectedEventsHash) {
    throw "Declaration events_csv_hash $declEventsHash != gate $expectedEventsHash"
}
if ($declLakeHash -ne $expectedLakeHash) {
    throw "Declaration lake_manifest_hash $declLakeHash != gate $expectedLakeHash"
}

$knownHostsDir = Join-Path $RepoRoot "runtime" "vast_known_hosts"
New-Item -ItemType Directory -Force -Path $knownHostsDir | Out-Null
$knownHostsFile = Join-Path $knownHostsDir "known_hosts"
$hostOnly = ($SshHost -split "@")[-1]
$keyscan = & ssh-keyscan -p $SshPort -H $hostOnly 2>$null
if (-not $keyscan) {
    throw "ssh-keyscan failed for ${hostOnly}:${SshPort} — fail-closed (no accept-new)"
}
$keyscan | Set-Content -Path $knownHostsFile -Encoding ascii
$sshOpts = @(
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$knownHostsFile",
    "-p", "$SshPort"
)
$scpOpts = @(
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$knownHostsFile",
    "-P", "$SshPort"
)

Write-Step "Push branch $GitBranch to origin"
if (-not $SkipPush) {
    & git push -u origin $GitBranch
    if ($LASTEXITCODE -ne 0) { throw "git push failed exit=$LASTEXITCODE" }
}
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& git fetch origin $GitBranch 2>&1 | Out-Null
$ErrorActionPreference = $prevEap
$localHead = (git rev-parse "origin/$GitBranch").Trim()
if ($localHead -ne $declHead) {
    throw "Origin/$GitBranch head $localHead != declaration git_head $declHead"
}

Write-Step "Remote repo sync"
$syncCmd = @'
set -euo pipefail
remote_repo="$1"
git_branch="$2"
if [[ -d "$remote_repo/.git" ]]; then
  git -C "$remote_repo" remote prune origin || true
  git -C "$remote_repo" fetch origin "$git_branch"
  git -C "$remote_repo" checkout -B "$git_branch" FETCH_HEAD
else
  git clone --branch "$git_branch" https://github.com/javin23863/hft3.git "$remote_repo"
fi
cd "$remote_repo"
echo REMOTE_HEAD=$(git rev-parse HEAD)
'@
Invoke-RemoteBash $syncCmd @($RemoteRepo, $GitBranch)
if ($LASTEXITCODE -ne 0) { throw "Remote sync failed exit=$LASTEXITCODE" }

Write-Step "SCP gate, declaration, events.csv, manifest.parquet"
$remoteGate = "$RemoteRepo/runtime/reports/paid_screen_ready_gate.json"
$remoteDecl = "$RemoteRepo/runtime/reports/vbt_full_run_declaration.json"
$remoteEvents = "$RemoteRepo/$EventsCsvRemote"
$mkdirCmd = @'
set -euo pipefail
mkdir -p "$@"
'@
Invoke-RemoteBash $mkdirCmd @(
    (Get-RemotePosixParent "remoteEvents" $remoteEvents),
    "$RemoteRepo/runtime/reports",
    (Get-RemotePosixParent "RemoteManifestPath" $RemoteManifestPath)
)
if ($LASTEXITCODE -ne 0) { throw "Remote mkdir failed exit=$LASTEXITCODE" }
& scp @scpOpts $gatePath "${SshHost}:${remoteGate}"
if ($LASTEXITCODE -ne 0) { throw "scp gate failed exit=$LASTEXITCODE" }
& scp @scpOpts $declPath "${SshHost}:${remoteDecl}"
if ($LASTEXITCODE -ne 0) { throw "scp declaration failed exit=$LASTEXITCODE" }
& scp @scpOpts $eventsPath "${SshHost}:${remoteEvents}"
if ($LASTEXITCODE -ne 0) { throw "scp events failed exit=$LASTEXITCODE" }
& scp @scpOpts $ManifestParquet "${SshHost}:${RemoteManifestPath}"
if ($LASTEXITCODE -ne 0) { throw "scp manifest failed exit=$LASTEXITCODE" }
$smokeUnits = Join-Path $RepoRoot "runtime/reports/vbt_smoke_units.jsonl"
if (-not (Test-Path $smokeUnits)) {
    Write-Step "Generate vbt_smoke_units.jsonl (missing locally)"
    & python (Join-Path $RepoRoot "scripts/generate_vbt_paid_units_jsonl.py") `
        --out $smokeUnits `
        --smoke-count 12 `
        --symbols "MES.v.0,ES.v.0" `
        --event-types "CPI,NFP" `
        --model-id "HYP_5"
    if ($LASTEXITCODE -ne 0) { throw "generate vbt_smoke_units failed exit=$LASTEXITCODE" }
}
if (-not (Test-Path $smokeUnits)) {
    throw "vbt_smoke_units.jsonl still missing after generation attempt"
}
& scp @scpOpts $smokeUnits "${SshHost}:${RemoteRepo}/runtime/reports/vbt_smoke_units.jsonl"
if ($LASTEXITCODE -ne 0) { throw "scp smoke units failed exit=$LASTEXITCODE" }

Write-Step "Verify remote HEAD + hashes + NPZ probe"
$remoteVerify = @'
set -euo pipefail
export DEPLOY_REPO="$1"
export DEPLOY_EVENTS="$2"
export DEPLOY_MANIFEST="$3"
export DEPLOY_HEAD="$4"
export DEPLOY_NPZ_ROOT="$5"
export DEPLOY_PROBE_N="$6"
exec bash "$DEPLOY_REPO/scripts/vast_remote_verify.sh"
'@
Invoke-RemoteBash $remoteVerify @($RemoteRepo, "$RemoteRepo/$EventsCsvRemote", $RemoteManifestPath, $localHead, $RemoteNpzRoot, "$ProbeUnitCount")
if ($LASTEXITCODE -ne 0) { throw "Remote verify failed exit=$LASTEXITCODE" }

Write-Step "NPZ parity probe (file counts)"
$localNpzCount = 0
if ($LocalNpzRoot -and (Test-Path $LocalNpzRoot)) {
    $localNpzCount = (Get-ChildItem $LocalNpzRoot -Filter *.npz -ErrorAction SilentlyContinue | Measure-Object).Count
}
$remoteNpzCmd = @'
set -euo pipefail
find "$1" -maxdepth 1 -type f -name '*.npz' 2>/dev/null | wc -l
'@
$remoteNpzCount = ((Invoke-RemoteBash $remoteNpzCmd @($RemoteNpzRoot)) | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0) { throw "Remote NPZ count failed exit=$LASTEXITCODE" }
Write-Host "local_npz=$localNpzCount remote_npz=$remoteNpzCount"
if ([int]$localNpzCount -gt 0 -and [int]$remoteNpzCount -lt 1) {
    throw "Remote NPZ root empty but local has $localNpzCount files"
}
if ([int]$localNpzCount -gt 0) {
    $ratio = [double]$remoteNpzCount / [double]$localNpzCount
    if ($ratio -lt 0.95) {
        Write-Warning "NPZ parity ratio $ratio (< 0.95): Vast partial lake ($remoteNpzCount vs local $localNpzCount). OK if NPZ probe passed."
        if ([int]$remoteNpzCount -lt 100) {
            throw "NPZ parity fail: remote count $remoteNpzCount too low"
        }
    }
}

Write-Host "`nDEPLOY_CONTRACT_PASS" -ForegroundColor Green
Write-Host "branch=$GitBranch head=$localHead ssh=${SshHost}:${SshPort} repo=$RemoteRepo"
