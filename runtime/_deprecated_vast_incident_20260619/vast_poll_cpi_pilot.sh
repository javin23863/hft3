#!/usr/bin/env bash
set -euo pipefail
RUN_ID="${1:-}"
if [[ -z "$RUN_ID" ]]; then
  echo "usage: $0 <run_id>" >&2
  exit 1
fi
cd /root/hft3/repo
M="research_cards/pipeline_runs/${RUN_ID}/paid_screen_run_manifest.json"
LOG="runtime/reports/${RUN_ID}.log"
echo -n "orch_procs="
pgrep -c -f '[p]ython.*run_vectorbt_paid_screen_v2' 2>/dev/null || echo 0
if [[ -f "$M" ]]; then
  python3 -c "import json; m=json.load(open('$M')); print('status',m.get('status'),'completed',m.get('completed_work_units'),'failed',m.get('failed_work_units'),'uph',m.get('units_per_hour'))"
else
  echo no_manifest
fi
echo -n "cpp_warnings="
grep -c 'hft3_features_cpp not found' "$LOG" 2>/dev/null || echo 0
grep -F '[drain]' "$LOG" 2>/dev/null | tail -3 || true
grep -F 'EXIT_CODE=' "$LOG" 2>/dev/null | tail -1 || true
