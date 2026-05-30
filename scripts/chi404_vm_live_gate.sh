#!/usr/bin/env bash
# Fail unless watch dir has recently growing live log files and bridge sees market events.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
WATCH="${RTRADER_WATCH_DIRS:-/root/hft3/rtrader_watch}"
GATE_MAX_AGE_MIN="${CHI404_LIVE_GATE_MAX_AGE_MIN:-10}"
GROW_WAIT_SEC="${CHI404_LIVE_GATE_GROW_SEC:-30}"

if [[ ! -d "$WATCH" ]]; then
  echo "FAIL: watch dir missing: $WATCH" >&2
  exit 1
fi

mapfile -t RECENT < <(
  find "$WATCH" \( -name '*.log' -o -name '*.cur.txt' \) -mmin "-${GATE_MAX_AGE_MIN}" 2>/dev/null \
    | grep -v rithmic_trial_smoke_export.log \
    | grep -v rithmic_trial_export.log \
    || true
)
if [[ ${#RECENT[@]} -eq 0 ]]; then
  echo "FAIL: no .log/.cur.txt modified in last ${GATE_MAX_AGE_MIN}m under $WATCH" >&2
  find "$WATCH" -maxdepth 2 -type f -printf '%TY-%Tm-%Td %TH:%TM %p %s\n' 2>/dev/null | tail -20 || true
  exit 1
fi

declare -A SIZE_BEFORE SIZE_AFTER
for f in "${RECENT[@]}"; do
  SIZE_BEFORE["$f"]=$(stat -c '%s' "$f" 2>/dev/null || echo 0)
done
echo "Waiting ${GROW_WAIT_SEC}s for watch file growth..."
sleep "$GROW_WAIT_SEC"
GROWTH=0
for f in "${RECENT[@]}"; do
  SIZE_AFTER["$f"]=$(stat -c '%s' "$f" 2>/dev/null || echo 0)
  if [[ "${SIZE_AFTER[$f]}" -gt "${SIZE_BEFORE[$f]}" ]]; then
    GROWTH=1
    echo "OK: grew $f (${SIZE_BEFORE[$f]} -> ${SIZE_AFTER[$f]})"
  fi
done
if [[ "$GROWTH" -ne 1 ]]; then
  echo "FAIL: no watch file grew in ${GROW_WAIT_SEC}s (session may be idle or stuck)" >&2
  exit 1
fi

echo "=== bridge poll probe ==="
export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rtrader
export RTRADER_WATCH_DIRS="$WATCH"
python3 - <<'PY'
import os
import sys
from pathlib import Path

repo = Path(os.environ.get("HFT3_REPO_DIR", "/root/hft3/repo"))
sys.path.insert(0, str(repo))

from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector import build_connector

cfg = load_config(repo / "data_system/config/rithmic_trial.yaml")
watch = os.environ.get("RTRADER_WATCH_DIRS", "/root/hft3/rtrader_watch")
cfg.rtrader["watch_dirs"] = [watch]

conn = build_connector(cfg)
conn.connect()
market = {"trade", "quote"}
found = set()
for _ in range(5):
    batch = conn.poll_events()
    for ev in batch:
        et = str(ev.get("event_type", "")).lower()
        if et in market:
            found.add(et)
    if found:
        break
    import time
    time.sleep(2)
conn.close()

if not found:
    print("FAIL: bridge poll found no trade/quote events in watch dir", file=sys.stderr)
    print(f"  watch={watch}", file=sys.stderr)
    sys.exit(1)
print(f"OK: bridge detected market event types: {sorted(found)}")
PY

echo "Live gate passed."
