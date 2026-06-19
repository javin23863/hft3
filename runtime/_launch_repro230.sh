#!/usr/bin/env bash
set -euo pipefail
cd /root/hft3/repo
RUN_ID="paid_pool_repro230_20260619T065404Z"
UNITS="runtime/reports/vbt_pool_repro_500.jsonl"
test -f "$UNITS" || head -500 runtime/reports/vbt_full_units.jsonl > "$UNITS"
if [[ -f /root/hft3/.env ]]; then set -a; source /root/hft3/.env; set +a; fi
export HFT3_NPZ_ROOT="${HFT3_NPZ_ROOT:-/data/npz}"
export HFT3_FEATURE_BACKEND=cpp
EVENTS_CSV="packages/data_system/config/events.csv"
read -r EVENTS_HASH LAKE_HASH < <(python3 scripts/vbt_hash_for_paid_screen.py 2>/dev/null || python3 -c "
import hashlib, json, os
from pathlib import Path
r=Path('/root/hft3/repo')
eh=hashlib.sha256((r/'packages/data_system/config/events.csv').read_bytes()).hexdigest()[:32]
decl=json.loads((r/'runtime/reports/vbt_full_run_declaration.json').read_text())
lh=str(decl.get('lake_manifest_hash') or '').strip()
print(eh, lh)
")
OUT="research_cards/pipeline_runs/${RUN_ID}"
mkdir -p "$OUT"
tmux kill-session -t vbt_pool_repro230 2>/dev/null || true
tmux new-session -d -s vbt_pool_repro230 bash -lc "cd /root/hft3/repo && export HFT3_NPZ_ROOT=$HFT3_NPZ_ROOT HFT3_FEATURE_BACKEND=cpp && python3 scripts/run_vectorbt_paid_screen_v2.py --units-jsonl $UNITS --out $OUT --workers 230 --owner-waiver pool_repro230 --vectorbt-scope paid-compute --events-csv $EVENTS_CSV --events-csv-hash $EVENTS_HASH --lake-manifest-hash $LAKE_HASH --batch-timeout-seconds 1200 --max-wall-clock-seconds 1800 2>&1 | tee runtime/reports/${RUN_ID}.log; echo EXIT=\$? >> runtime/reports/${RUN_ID}.log"
echo LAUNCHED run_id=$RUN_ID
echo $RUN_ID > /tmp/vbt_pool_repro230_run_id.txt