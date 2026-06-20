#!/bin/bash
RUN_ID="paid_full_v2_20260618T235532Z"
OUT="/root/hft3/repo/research_cards/pipeline_runs/${RUN_ID}"
LOG="/root/vbt_full_v2.log"
SCRATCH="/root/hft3/repo/runtime/paid_screen_scratch/${RUN_ID}"
MON="/root/vbt_full_v2_cost_monitor.jsonl"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 - "$TS" "$RUN_ID" "$OUT" "$LOG" "$SCRATCH" "$MON" << 'PYEOF'
import json, os, subprocess, glob, sys, re
from datetime import datetime, timezone
ts, run_id, out, log_path, scratch, mon = sys.argv[1:7]

def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=60).strip()
    except Exception as e:
        return f"ERROR: {e}"

def int0(s):
    m = re.search(r'\d+', str(s).split('\n')[0] or '0')
    return int(m.group()) if m else 0

tmux = sh("tmux list-sessions 2>/dev/null | grep -E 'vbt_full_v2' || echo 'no session'")
tmux_panes = sh("tmux capture-pane -t vbt_full_v2 -p -S -40 2>/dev/null | tail -20 || echo ''")
rpc = sh("pgrep -af 'run_paid_screen.py' 2>/dev/null | grep -v pgrep || true")
rv2 = sh("pgrep -af 'run_vectorbt_paid_screen_v2.py' 2>/dev/null | grep -v pgrep || true")
rpipe = sh("pgrep -af 'run_pipeline.py' 2>/dev/null | grep -v pgrep || true")
rpc_n = len([l for l in rpc.split('\n') if l.strip()])
rv2_n = len([l for l in rv2.split('\n') if l.strip()])
rpipe_n = len([l for l in rpipe.split('\n') if l.strip()])
worker_py = int0(sh("pgrep -c -f 'paid_screen_worker' 2>/dev/null || echo 0"))
loadavg = sh("cat /proc/loadavg")
cpu = sh("top -bn1 | head -5")
manifest_path = os.path.join(out, "running_manifest.json")
manifest = None
if os.path.isfile(manifest_path):
    with open(manifest_path) as f:
        manifest = json.load(f)
drain_count = 0
last_drain = None
if os.path.isfile(log_path):
    drain_count = int0(sh(f"grep -c '\\[drain\\]' {log_path} 2>/dev/null || echo 0"))
    with open(log_path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        read_sz = min(size, 800000)
        f.seek(max(0, size - read_sz))
        tail = f.read().decode('utf-8', errors='replace')
    drain_lines = [l for l in tail.split('\n') if '[drain]' in l]
    if drain_lines:
        last_drain = drain_lines[-1][:800]
    log_tail = tail[-3000:]
    log_mtime = datetime.fromtimestamp(os.path.getmtime(log_path), tz=timezone.utc).isoformat()
else:
    log_tail = "missing"
    log_mtime = None
units_dir = os.path.join(out, "units")
unit_count = len([d for d in os.listdir(units_dir) if os.path.isdir(os.path.join(units_dir, d))]) if os.path.isdir(units_dir) else 0
unit_artifacts = len(glob.glob(os.path.join(units_dir, "*/screening_artifact.json")))
legacy_scratch = os.path.join(out, ".worker_scratch")
legacy_exists = os.path.isdir(legacy_scratch)
scratch_count = 0
scratch_bytes = 0
if os.path.isdir(scratch):
    for root, dirs, files in os.walk(scratch):
        scratch_count += len(files)
        for fn in files:
            try:
                scratch_bytes += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
root_artifact = os.path.isfile(os.path.join(out, "screening_artifact.json"))
git_head = sh("cd /root/hft3/repo && git rev-parse --short HEAD 2>/dev/null || echo unknown")
record = {
    "poll_ts_utc": ts,
    "run_id": run_id,
    "git_head": git_head,
    "tmux": tmux,
    "tmux_tail": tmux_panes[-1000:] if tmux_panes else "",
    "processes": {
        "run_paid_screen_count": rpc_n,
        "run_vectorbt_paid_screen_v2_count": rv2_n,
        "run_pipeline_count": rpipe_n,
        "paid_screen_worker_count": worker_py,
        "run_paid_screen_sample": rpc[:500],
        "run_v2_sample": rv2[:500],
        "run_pipeline_sample": rpipe[:800] if rpipe_n else "",
    },
    "loadavg": loadavg,
    "cpu_head": cpu,
    "manifest": manifest,
    "manifest_path_exists": os.path.isfile(manifest_path),
    "log": {
        "path": log_path,
        "mtime_utc": log_mtime,
        "drain_line_count": drain_count,
        "last_drain_line": last_drain,
        "tail": log_tail,
    },
    "artifacts": {
        "unit_dirs": unit_count,
        "unit_screening_artifacts": unit_artifacts,
        "root_screening_artifact": root_artifact,
        "scratch_files": scratch_count,
        "scratch_bytes": scratch_bytes,
        "legacy_worker_scratch_exists": legacy_exists,
    },
    "expected_units": 1629250,
    "workers_config": 375,
    "cost_per_hr_usd": 1.1389,
}
line = json.dumps(record)
with open(mon, "a") as mf:
    mf.write(line + "\n")
print(line)
PYEOF
