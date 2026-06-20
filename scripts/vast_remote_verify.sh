#!/usr/bin/env bash
# Remote-side deploy contract checks (called by vast_deploy_and_verify.ps1).
set -euo pipefail

REPO="${DEPLOY_REPO:?DEPLOY_REPO required}"
EVENTS="${DEPLOY_EVENTS:?DEPLOY_EVENTS required}"
MANIFEST="${DEPLOY_MANIFEST:?DEPLOY_MANIFEST required}"
EXPECTED_HEAD="${DEPLOY_HEAD:?DEPLOY_HEAD required}"
NPZ_ROOT="${DEPLOY_NPZ_ROOT:-/data/npz}"
PROBE_N="${DEPLOY_PROBE_N:-20}"

cd "$REPO"

head="$(git rev-parse HEAD)"
events_h="$(python3 - <<'PY'
import hashlib, os, sys
from pathlib import Path
p = Path(os.environ["DEPLOY_EVENTS"])
print(hashlib.sha256(p.read_bytes()).hexdigest()[:32])
PY
)"
lake_h="$(python3 - <<'PY'
import hashlib, os, sys
from pathlib import Path
p = Path(os.environ["DEPLOY_MANIFEST"])
print(hashlib.sha256(p.read_bytes()).hexdigest()[:32])
PY
)"

gate_events="$(python3 - <<'PY'
import json, os
from pathlib import Path
repo = Path(os.environ["DEPLOY_REPO"])
gate = json.loads((repo / "runtime/reports/paid_screen_ready_gate.json").read_text(encoding="utf-8"))
print(gate["pilot_hashes"]["events_csv_hash"])
PY
)"
gate_lake="$(python3 - <<'PY'
import json, os
from pathlib import Path
repo = Path(os.environ["DEPLOY_REPO"])
gate = json.loads((repo / "runtime/reports/paid_screen_ready_gate.json").read_text(encoding="utf-8"))
print(gate["pilot_hashes"]["lake_manifest_hash"])
PY
)"

echo "REMOTE_HEAD=$head"
echo "EVENTS_HASH=$events_h"
echo "LAKE_HASH=$lake_h"

if [[ "$head" != "$EXPECTED_HEAD" ]]; then
  echo "FAIL: HEAD mismatch" >&2
  exit 1
fi
if [[ "$events_h" != "$gate_events" ]]; then
  echo "FAIL: events hash mismatch" >&2
  exit 1
fi
if [[ "$lake_h" != "$gate_lake" ]]; then
  echo "FAIL: lake hash mismatch" >&2
  exit 1
fi
echo "HASH_VERIFY_OK"

remote_npz="$(find "$NPZ_ROOT" -maxdepth 1 -type f -name '*.npz' 2>/dev/null | wc -l | tr -d ' ')"
echo "REMOTE_NPZ_COUNT=$remote_npz"

export HFT3_NPZ_ROOT="$NPZ_ROOT"
export HFT3_MANIFEST_PATH="$MANIFEST"
python3 - <<PY
import json, os, sys
from pathlib import Path

repo = Path(os.environ["DEPLOY_REPO"])
sys.path.insert(0, str(repo))
from hft3_bootstrap import setup_repo_paths
setup_repo_paths()
from backtest_pipeline.src.vectorbt_adapter import _npz_candidates_for_event
from data_system.src.event_data_resolver import npz_search_dirs

probe_n = int(os.environ.get("DEPLOY_PROBE_N", "20"))
smoke = repo / "runtime/reports/vbt_smoke_units.jsonl"
units_path = smoke if smoke.is_file() else repo / "runtime/reports/vbt_full_units.jsonl"
if not units_path.is_file():
    sys.stderr.write("FAIL: no probe units JSONL\\n")
    sys.exit(1)
rows = [json.loads(l) for l in units_path.read_text(encoding="utf-8").splitlines() if l.strip()]
sample = rows[:probe_n]
hits = sum(
    1 for u in sample
    if _npz_candidates_for_event(npz_search_dirs(repo), u.get("event_id"), u.get("symbol"))
)
print("PROBE_HITS=" + str(hits) + "/" + str(len(sample)))
if hits != len(sample):
    sys.stderr.write("FAIL: NPZ resolution probe\\n")
    sys.exit(1)
PY

echo "DEPLOY_CONTRACT_PASS"
