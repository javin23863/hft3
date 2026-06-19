# Prepare Vast host + workstation for VectorBT paid-screen backtesting.
# Authority: docs/project/VBT_PAID_SCREEN_RUNBOOK.md, wiki/hot.md (Phases 0-9).
# Budget: instance start wait up to 15 minutes; SSH probes 60s.
param(
    [string]$InstanceId = $(if ($env:HFT3_VAST_INSTANCE_ID) { $env:HFT3_VAST_INSTANCE_ID } else { "41496444" }),
    [int]$StartWaitMin = 15,
    [string]$RepoRoot = $(if ($env:HFT3_REPO) { $env:HFT3_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }),
    [string]$RemoteRepo = "/root/hft3/repo",
    [string]$GitBranch = $(if ($env:HFT3_VAST_GIT_BRANCH) { $env:HFT3_VAST_GIT_BRANCH } else { "cursor/vast-vbt-workflow" })
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

function Get-VastSshTarget {
    param([string]$Id)
    $raw = (& vastai show instance $Id --raw 2>&1 | Out-String).Trim()
    if (-not $raw) { throw "vastai show instance $Id failed" }
    $obj = $raw | ConvertFrom-Json
    return @{
        Status = $obj.actual_status
        Host = "root@$($obj.ssh_host)"
        Port = [int]$obj.ssh_port
        EffectiveCores = [double]$obj.cpu_cores_effective
        DiskGb = [double]$obj.disk_space
        Label = $obj.label
    }
}

function Wait-VastRunning {
    param([string]$Id, [int]$MaxMin)
    $deadline = (Get-Date).AddMinutes($MaxMin)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $t = Get-VastSshTarget -Id $Id
        $last = $t
        Write-Host ("poll status={0} ssh={1}:{2} eff_cores={3}" -f $t.Status, $t.Host, $t.Port, $t.EffectiveCores)
        if ($t.Status -eq "running") { return $t }
        if ($t.Status -in @("exited", "stopped")) {
            & vastai start instance $Id 2>&1 | Out-Host
        }
        Start-Sleep -Seconds 30
    }
    throw "Instance $Id not running after ${MaxMin}m (last status=$($last.Status))"
}

Write-Step "Workstation preflight"
Set-Location $RepoRoot
$head = (git rev-parse --short HEAD).Trim()
Write-Host "repo=$RepoRoot head=$head branch=$(git branch --show-current)"

$npzRoot = if ($env:HFT3_NPZ_ROOT) { $env:HFT3_NPZ_ROOT } elseif (Test-Path "C:\hft3-lake\npz") { "C:\hft3-lake\npz" } else { $null }
if ($npzRoot) {
    $npzCount = (Get-ChildItem $npzRoot -Filter *.npz -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "HFT3_NPZ_ROOT=$npzRoot npz_count=$npzCount"
} else {
    Write-Warning "HFT3_NPZ_ROOT not set and C:\hft3-lake\npz missing"
}

$py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $py) {
    $phaseJson = & $py scripts/vbt_paid_screen_next_steps.py --json 2>&1 | Out-String
    Write-Host $phaseJson
} else {
    Write-Warning ".venv missing — run python -m venv .venv && pip install -r apps/workbench/requirements.txt"
}

Write-Step "Vast instance $InstanceId"
$tgt = $null
try {
    $tgt = Wait-VastRunning -Id $InstanceId -MaxMin $StartWaitMin
} catch {
    Write-Warning $_.Exception.Message
    Write-Host "BLOCKED: instance not running. Retry later or rent a 256 vCPU host per VBT_PAID_SCREEN_RUNBOOK.md."
    exit 2
}

$sshHost = $tgt.Host
$sshPort = $tgt.Port
$workers = [Math]::Max(1, [int]([Math]::Floor($tgt.EffectiveCores) - 4))
Write-Host "recommended_workers=$workers (effective_cores=$($tgt.EffectiveCores))"

Write-Step "Remote sync + host preflight"
$remoteCmd = @"
set -euo pipefail
if [[ -d $RemoteRepo/.git ]]; then
  git -C $RemoteRepo fetch origin
  git -C $RemoteRepo checkout $GitBranch
  git -C $RemoteRepo pull --ff-only origin $GitBranch || true
else
  git clone --branch $GitBranch https://github.com/javin23863/hft3.git $RemoteRepo
fi
cd $RemoteRepo
echo HEAD=\$(git rev-parse --short HEAD)
echo nproc=\$(nproc)
echo npz_root=\${HFT3_NPZ_ROOT:-/data/npz}
if [[ -d \${HFT3_NPZ_ROOT:-/data/npz} ]]; then
  find \${HFT3_NPZ_ROOT:-/data/npz} -maxdepth 1 -type f -name '*.npz' 2>/dev/null | wc -l
else
  echo npz_missing
fi
test -f runtime/reports/paid_screen_ready_gate.json && head -c 200 runtime/reports/paid_screen_ready_gate.json || echo gate_missing
"@

& ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -p $sshPort $sshHost $remoteCmd

Write-Step "Remote deps (vectorbt rust + handoff verify + pandas pin)"
$depsCmd = @"
cd $RemoteRepo && bash scripts/install_vbt_hbt_handoff_verify_deps.sh && \
pip3 install -q 'pandas<3.0' 'vectorbt[rust]==1.0.0' pybind11 numpy && \
python3 -c "import vectorbt; print('vectorbt', vectorbt.__version__)"
"@
& ssh -o ConnectTimeout=20 -p $sshPort $sshHost $depsCmd

Write-Step "Remote hft3_features_cpp build"
$cppCmd = @"
set -euo pipefail
cd $RemoteRepo
if ! command -v cmake >/dev/null 2>&1; then
  apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y cmake build-essential python3-dev
fi
PYBIND_DIR=\$(python3 -m pybind11 --cmakedir)
cmake -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="\$PYBIND_DIR"
cmake --build build --target hft3_features_cpp -j"\$(nproc)"
python3 -c "
import sys
sys.path.insert(0, '.')
from features._cpp_loader import load_cpp_features
m = load_cpp_features()
assert m is not None, 'hft3_features_cpp import failed'
print('cpp_ok', m)
"
NPZ=\$(find \${HFT3_NPZ_ROOT:-/data/npz} -name '*.npz' -size +10k 2>/dev/null | head -1)
if [[ -n "\$NPZ" ]]; then
  python3 -S scripts/verify_cpp_parity.py --npz "\$NPZ" || echo "parity_warn: verify_cpp_parity failed (non-fatal)"
else
  echo "parity_skip: no npz"
fi
"@
& ssh -o ConnectTimeout=120 -p $sshPort $sshHost $cppCmd

Write-Host "`nREADY CHECKLIST:"
Write-Host "  [ ] Phase A pilot on workstation (vectorbt[rust] + run_pipeline --vectorbt-scope pilot)"
Write-Host "  [ ] Phase B smoke (bash scripts/run_vbt_paid_screen_smoke.sh)"
Write-Host "  [ ] Phase C gate (validate_paid_screen_ready_gate.py — expect paid_screen_gate_not_allowed until manifest flip)"
Write-Host "  [ ] Phase D units: generated on Vast via run_vbt_paid_screen_vast_full.sh (--all-active-models × events.csv)"
Write-Host "  [ ] VBT_WORKERS=$workers on this host (not 230 unless true 256-core rent)"
Write-Host "  [ ] Sync gate: scp runtime/reports/paid_screen_ready_gate.json ${sshHost}:$RemoteRepo/runtime/reports/"
Write-Host "  [ ] Full run: ssh -p $sshPort $sshHost 'cd $RemoteRepo && VBT_WORKERS=$workers bash scripts/run_vbt_paid_screen_vast_full.sh'"
