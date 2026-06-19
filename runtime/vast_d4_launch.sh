#!/usr/bin/env bash
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT=/data/npz
# ponytail: gate lineage uses manifest.parquet hash (cc910af8…), not manifest.json
export HFT3_MANIFEST_PATH=/data/npz/manifest.parquet
export HFT3_FEATURE_BACKEND=cpp
export VBT_RESUME=0

python3 - <<'PY'
import hashlib, json, os
p = os.environ["HFT3_MANIFEST_PATH"]
h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:32]
g = json.load(open("runtime/reports/paid_screen_ready_gate.json"))
exp = g["pilot_hashes"]["lake_manifest_hash"]
print("lake_hash", h)
print("expected", exp)
print("match", h == exp)
if h != exp:
    raise SystemExit("lake manifest hash mismatch — abort")
PY

echo "=== Launch full paid screen (230 workers) ==="
RUN_ID="paid_full_v2_$(date -u +%Y%m%dT%H%M%SZ)"
export VBT_FULL_RUN_ID="$RUN_ID"
tmux kill-session -t vbt_full_v2 2>/dev/null || true
tmux new -d -s vbt_full_v2 \
  "cd /root/hft3/repo && export HFT3_NPZ_ROOT=/data/npz HFT3_MANIFEST_PATH=/data/npz/manifest.parquet HFT3_FEATURE_BACKEND=cpp VBT_FULL_RUN_ID=${RUN_ID} VBT_RESUME=0 && bash scripts/run_vbt_paid_screen_vast_full.sh 2>&1 | tee /root/vbt_full_v2.log; echo EXIT_CODE=\$? >> /root/vbt_full_v2.log"

sleep 3
tmux ls
echo "LAUNCHED_FULL ${RUN_ID}"
tail -n 15 /root/vbt_full_v2.log 2>/dev/null || true
