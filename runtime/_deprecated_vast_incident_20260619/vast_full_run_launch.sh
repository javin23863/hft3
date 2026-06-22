#!/usr/bin/env bash
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT=/data/npz
export HFT3_MANIFEST_PATH=/data/npz/manifest.json
export HFT3_FEATURE_BACKEND=cpp

UNITS="$(wc -l < runtime/reports/vbt_full_units.jsonl)"
RUN_ID="paid_full_v2_$(date -u +%Y%m%dT%H%M%SZ)"
GIT_HEAD="$(git rev-parse HEAD)"
LAKE_HASH="$(python3 -c "import hashlib;print(hashlib.sha256(open('/data/npz/manifest.json','rb').read()).hexdigest()[:32])")"
EVENTS_HASH="$(python3 -c "import hashlib;print(hashlib.sha256(open('packages/data_system/config/events.csv','rb').read()).hexdigest()[:32])")"

python3 - "$UNITS" "$RUN_ID" "$GIT_HEAD" "$LAKE_HASH" "$EVENTS_HASH" <<'PY'
import json, sys
units, run_id, git_head, lake_hash, events_hash = sys.argv[1:6]
decl = {
    "host_vcpu": 384,
    "reserved_vcpu": 26,
    "workers_requested": 230,
    "expected_work_units": int(units),
    "run_id": run_id,
    "vbt_full_run_id": run_id,
    "units_source": "runtime/reports/vbt_full_units.jsonl (50 models x TIGHT events x 7 symbols)",
    "stall_minutes": 45,
    "abort_on_failed_units": False,
    "git_head": git_head,
    "events_csv_hash": events_hash,
    "lake_manifest_hash": lake_hash,
    "ready_gate_file": "runtime/reports/paid_screen_ready_gate.json",
    "data_mode": "vast_npz_lake_cpp_features",
    "smoke_units_per_hour": 128.36,
    "medium_units_per_hour": 319.89,
}
path = "runtime/reports/vbt_full_run_declaration.json"
open(path, "w", encoding="utf-8").write(json.dumps(decl, indent=2) + "\n")
print("wrote", path, "units", units, "run_id", run_id)
PY

export VBT_FULL_RUN_ID="$RUN_ID"
export VBT_RESUME=0
tmux kill-session -t vbt_full_v2 2>/dev/null || true
tmux new -d -s vbt_full_v2 \
  "cd /root/hft3/repo && export HFT3_NPZ_ROOT=/data/npz HFT3_MANIFEST_PATH=/data/npz/manifest.json HFT3_FEATURE_BACKEND=cpp VBT_FULL_RUN_ID=${RUN_ID} VBT_RESUME=0 && bash scripts/run_vbt_paid_screen_vast_full.sh 2>&1 | tee /root/vbt_full_v2.log; echo EXIT_CODE=\$? >> /root/vbt_full_v2.log"

echo "LAUNCHED_FULL ${RUN_ID}"
