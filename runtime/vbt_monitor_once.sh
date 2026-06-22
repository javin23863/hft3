#!/usr/bin/env bash
LOG=/root/vbt_full_v2.log
RUN_DIR=$(ls -td /root/hft3/repo/research_cards/pipeline_runs/paid_full* 2>/dev/null | head -1)
echo "=== run_dir=$RUN_DIR ==="
echo "=== log lines=$(wc -l < "$LOG" 2>/dev/null || echo 0) ==="
tail -30 "$LOG" 2>/dev/null

echo "=== manifest ==="
MANIFEST="${RUN_DIR}/paid_screen_run_manifest.json"
if [[ -f "$MANIFEST" ]]; then
  ls -la "$MANIFEST"
  python3 - "$MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
for k in ("run_id","status","units_completed","units_total","workers","started_at"):
    if k in m: print(f"{k}={m[k]}")
PY
else
  echo "manifest not yet"
fi

echo "=== worker procs ==="
pgrep -af run_paid_screen | head -5 || echo "no run_paid_screen"
pgrep -c -f paid_screen_worker 2>/dev/null || echo "worker_count=0"

echo "=== error counts ==="
echo -n "signal_computer_failed="; grep -c "signal computer failed" "$LOG" 2>/dev/null || echo 0
echo -n "dict_action="; grep -c "dict.*action" "$LOG" 2>/dev/null || echo 0
echo -n "unit_ok_bracket="; grep -c "\[unit\] -> OK" "$LOG" 2>/dev/null || echo 0
echo -n "unit_err_bracket="; grep -c "\[unit\] -> ERROR" "$LOG" 2>/dev/null || echo 0
echo -n "unit_ok_plain="; grep -cE "unit.*-> OK|status=ok|unit_ok" "$LOG" 2>/dev/null || echo 0
echo -n "unit_err_plain="; grep -cE "unit.*-> ERROR|status=error|unit_error" "$LOG" 2>/dev/null || echo 0

ORCH="${RUN_DIR}/orchestrator.log"
if [[ -f "$ORCH" ]]; then
  echo "=== orchestrator.log ==="
  echo -n "orch_lines="; wc -l < "$ORCH"
  echo -n "orch_signal_fail="; grep -c "signal computer failed" "$ORCH" 2>/dev/null || echo 0
  echo -n "orch_dict_action="; grep -c "dict.*action" "$ORCH" 2>/dev/null || echo 0
  echo -n "orch_unit_ok="; grep -c "\[unit\] -> OK" "$ORCH" 2>/dev/null || echo 0
  echo -n "orch_unit_err="; grep -c "\[unit\] -> ERROR" "$ORCH" 2>/dev/null || echo 0
  grep "\[unit\] ->" "$ORCH" 2>/dev/null | head -200 | awk '{print $NF}' | sort | uniq -c
  tail -15 "$ORCH"
fi

echo "=== manifest progress ==="
python3 - "$MANIFEST" <<'PY'
import json, sys, os
p = sys.argv[1]
if not os.path.isfile(p):
    print("no manifest")
    raise SystemExit
m = json.load(open(p))
for k in sorted(m.keys()):
    if any(x in k.lower() for x in ("unit","complete","error","ok","worker","status","progress","count")):
        print(f"{k}={m[k]}")
units = m.get("units") or m.get("unit_results") or []
if isinstance(units, list) and units:
    from collections import Counter
    c = Counter(u.get("status") if isinstance(u, dict) else str(u) for u in units[:500])
    print("first500_status=", dict(c))
PY

echo "=== first 200 unit outcomes (main log) ==="
grep "\[unit\] ->" "$LOG" 2>/dev/null | head -200 | awk '{print $NF}' | sort | uniq -c

echo "=== feature env ==="
grep -E 'HFT3_FEATURE|feature_root|fs_v1' /root/hft3/.env 2>/dev/null || true
env | grep -E 'HFT3_FEATURE|HFT3_NPZ' || true

echo "=== run_id from log ==="
grep -E 'Starting full run id=|Run id from declaration' "$LOG" 2>/dev/null | tail -2
