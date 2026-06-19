#!/usr/bin/env bash
set -euo pipefail
RUN_ID="paid_full_v2_20260619T005057Z"
OUT="/root/hft3/repo/research_cards/pipeline_runs/${RUN_ID}"
echo "RUN_ID=${RUN_ID}"
echo "tmux=$(tmux list-sessions 2>/dev/null | grep vbt_full_v2 || echo none)"
echo "orchestrator=$(pgrep -af 'scripts/run_paid_screen.py' | grep -v pgrep | head -1 || true)"
echo "v2=$(pgrep -af 'run_vectorbt_paid_screen_v2.py' | grep -v pgrep | head -1 || true)"
echo "worker_count=$(pgrep -c -f paid_screen_worker 2>/dev/null || echo 0)"
echo "resume_flag=$(pgrep -af 'scripts/run_paid_screen.py' | grep -o -- '--resume' | wc -l)"
ls -la "${OUT}" 2>/dev/null || echo "OUT missing"
for f in running_manifest.json paid_screen_run_manifest.json orchestrator.log; do
  if [[ -f "${OUT}/${f}" ]]; then echo "has_${f}=yes size=$(wc -c < "${OUT}/${f}")"; else echo "has_${f}=no"; fi
done
if [[ -f "${OUT}/orchestrator.log" ]]; then
  echo "signal_failed=$(grep -c 'signal computer failed' "${OUT}/orchestrator.log" || true)"
  echo "ok_units=$(grep -c '\[unit\] -> OK' "${OUT}/orchestrator.log" || true)"
  echo "failed_units=$(grep -c '\[unit\] -> FAILED' "${OUT}/orchestrator.log" || true)"
  echo "no_ohlcv=$(grep -c 'no_ohlcv_data' "${OUT}/orchestrator.log" || true)"
  echo "---first100---"
  grep -E '\[unit\] ->|signal computer failed' "${OUT}/orchestrator.log" | head -100
fi
if [[ -f "${OUT}/paid_screen_run_manifest.json" ]]; then
  python3 - "${OUT}/paid_screen_run_manifest.json" <<'PY'
import json, sys
from collections import Counter
m = json.load(open(sys.argv[1], encoding="utf-8"))
for k in ("status","stop_reason","aborted","resume","started_at_utc","finished_at_utc","completed_work_units","failed_work_units","skipped_work_units","expected_work_units"):
    print(f"manifest_{k}={m.get(k)}")
ur = m.get("unit_results") or {}
if isinstance(ur, dict) and ur:
    c = Counter(v.get("status") if isinstance(v, dict) else str(v) for v in ur.values())
    print("manifest_unit_results_status=" + str(dict(c)))
    print(f"manifest_unit_results_count={len(ur)}")
PY
fi
echo "---log_tail---"
tail -15 /root/vbt_full_v2.log
