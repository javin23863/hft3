#!/usr/bin/env bash
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT=/data/npz
# Gate pilot_hashes.lake_manifest_hash is sha256(manifest.parquet) — not manifest.json
export HFT3_MANIFEST_PATH=/data/npz/manifest.parquet
export HFT3_FEATURE_BACKEND=cpp

echo "=== NPZ lake ==="
find /data/npz -maxdepth 1 -name '*.npz' 2>/dev/null | wc -l
python3 - <<'PY'
import hashlib, json, os, sys
p = os.environ["HFT3_MANIFEST_PATH"]
if not os.path.isfile(p):
    print(f"ERROR: manifest missing: {p}", file=sys.stderr)
    sys.exit(1)
h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:32]
g = json.load(open("runtime/reports/paid_screen_ready_gate.json"))
if not g.get("ready_for_full_run"):
    print("ERROR: ready_for_full_run is false", file=sys.stderr)
    sys.exit(1)
exp = g["pilot_hashes"]["lake_manifest_hash"]
print("lake_hash", h)
print("expected", exp)
print("gate_ready", True)
if h != exp:
    print("ERROR: lake manifest hash mismatch", file=sys.stderr)
    sys.exit(1)
print("match", True)
PY

echo "=== Preflight handoff (bounded 180s) ==="
bash scripts/run_vbt_hbt_handoff_verify.sh 2>&1 | tail -n 40 || echo "WARN: handoff verify non-zero or timeout (see log)"

echo "=== tmux sessions before launch ==="
tmux ls 2>/dev/null || echo "no tmux"
