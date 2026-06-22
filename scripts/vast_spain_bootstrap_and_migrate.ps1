# Bootstrap Spain Vast + migrate from Belgium when source is up.
param(
    [string]$SpainHost = "root@ssh8.vast.ai",
    [int]$SpainPort = 22954,
    [string]$BelgiumHost = "root@ssh7.vast.ai",
    [int]$BelgiumPort = 15808,
    [int]$BelgiumInstanceId = 41655809,
    [string]$GitBranch = "main",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
$sshSpain = @("-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=accept-new", "-p", "$SpainPort")
$sshBe = @("-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=accept-new", "-p", "$BelgiumPort")

function Wait-BelgiumRunning {
    for ($i = 0; $i -lt 30; $i++) {
        vastai start instance $BelgiumInstanceId 2>&1 | Out-Null
        $raw = vastai show instance $BelgiumInstanceId --raw 2>&1 | Out-String
        $j = $raw | ConvertFrom-Json
        if ($j.cur_state -eq "running" -and $j.actual_status -eq "running") { return $true }
        Write-Host "Belgium not running yet (state=$($j.cur_state)); retry in 60s..."
        Start-Sleep -Seconds 60
    }
    return $false
}

Write-Host "=== Spain bootstrap: repo + env ===" -ForegroundColor Cyan
& ssh @sshSpain $SpainHost @"
set -euo pipefail
mkdir -p /root/hft3/repo/runtime/reports /data/npz
if [[ -d /root/hft3/repo/.git ]]; then
  git -C /root/hft3/repo fetch origin $GitBranch
  git -C /root/hft3/repo checkout -B $GitBranch origin/$GitBranch || git -C /root/hft3/repo checkout $GitBranch
else
  git clone --branch $GitBranch https://github.com/javin23863/hft3.git /root/hft3/repo
fi
export HFT3_NPZ_ROOT=/data/npz
export HFT3_MANIFEST_PATH=/data/npz/manifest.parquet
echo HEAD=\$(git -C /root/hft3/repo rev-parse HEAD)
"@

$gate = Join-Path $RepoRoot "runtime/reports/paid_screen_ready_gate.json"
$events = Join-Path $RepoRoot "packages/data_system/config/events.csv"
& scp @("-P", "$SpainPort", "-o", "ConnectTimeout=20") $gate "${SpainHost}:/root/hft3/repo/runtime/reports/paid_screen_ready_gate.json"
& scp @("-P", "$SpainPort", "-o", "ConnectTimeout=20") $events "${SpainHost}:/root/hft3/repo/packages/data_system/config/events.csv"

Write-Host "=== Wait for Belgium source ===" -ForegroundColor Cyan
if (-not (Wait-BelgiumRunning)) {
    Write-Warning "Belgium $BelgiumInstanceId still unavailable. Falling back to local NPZ tar-chunk upload."
    python (Join-Path $RepoRoot "scripts/vast_upload_npz_tar_chunks.py") --ssh-port $SpainPort --chunks 12
} else {
    Write-Host "=== Belgium->Spain fast parallel rsync ===" -ForegroundColor Cyan
    $env:BE_SSH = $BelgiumHost
    $env:BE_PORT = "$BelgiumPort"
    $env:ES_SSH = $SpainHost
    $env:ES_PORT = "$SpainPort"
    $env:PARALLEL = "16"
    bash (Join-Path $RepoRoot "scripts/vast_migrate_belgium_to_spain.sh")
}

Write-Host "=== Spain: install deps + launch full run (300 workers) ===" -ForegroundColor Cyan
$newRun = "paid_full_spain_$(Get-Date -Format 'yyyyMMddTHHmmss')Z"
& ssh @sshSpain $SpainHost @"
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT=/data/npz
export HFT3_MANIFEST_PATH=/data/npz/manifest.parquet
export VBT_WORKERS=300
export VBT_FULL_RUN_ID='$newRun'
bash scripts/install_vbt_hbt_handoff_verify_deps.sh || true
pip install -q 'vectorbt[rust]==1.0.0' || true
tmux kill-session -t vbt_full 2>/dev/null || true
tmux new-session -d -s vbt_full 'cd /root/hft3/repo && export HFT3_NPZ_ROOT=/data/npz HFT3_MANIFEST_PATH=/data/npz/manifest.parquet VBT_WORKERS=300 VBT_RESUME=1 bash scripts/run_vbt_paid_screen_vast_full.sh 2>&1 | tee /root/vbt_full_spain.log'
echo LAUNCHED run_id=$newRun
"@

Write-Host "Spain instance 42062955 ssh ${SpainHost}:${SpainPort}" -ForegroundColor Green
Write-Host "Logs: ssh -p $SpainPort $SpainHost -t tmux attach -t vbt_full"
