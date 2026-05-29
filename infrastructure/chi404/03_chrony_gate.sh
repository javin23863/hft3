#!/bin/bash
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
CRITERIA="$(dirname "$0")/PASS_CRITERIA.json"
[[ -f /root/hft3/repo/infrastructure/chi404/PASS_CRITERIA.json ]] && CRITERIA=/root/hft3/repo/infrastructure/chi404/PASS_CRITERIA.json

EPS_HALT=$(python3 -c "import json; print(json.load(open('$CRITERIA'))['epsilon_halt_us'])")
RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"

INFRA="$(cd "$(dirname "$0")/.." && pwd)"
bash "$INFRA/02_clock_sync.sh" 2>&1 | tee "$LOG_DIR/chrony_install.txt"

FAIL=0
for i in 1 2 3; do
  chronyc tracking | tee -a "$LOG_DIR/chrony_gate.txt"
  OFF_US=$(chronyc tracking | awk -F': ' '/RMS offset/ {print $2}' | awk '{v=$1; if ($2 ~ /ms/) print v*1000; else if ($2 ~ /s/) print v*1000000; else print v}')
  # simpler: use Python
  OFF_US=$(python3 << PY
import re, subprocess
t = subprocess.check_output(["chronyc","tracking"], text=True)
m = re.search(r"RMS offset\s*:\s*([0-9.]+)\s*(\S+)", t)
if not m:
    print(999999); raise SystemExit
v, u = float(m.group(1)), m.group(2).lower()
if u.startswith("ms"): print(int(v*1000))
elif u.startswith("s"): print(int(v*1_000_000))
else: print(int(v))
PY
)
  echo "sample $i RMS offset ~${OFF_US} us (halt at ${EPS_HALT} us)" | tee -a "$LOG_DIR/chrony_gate.txt"
  if [[ "$OFF_US" -gt "$EPS_HALT" ]]; then
    FAIL=1
  fi
  sleep 20
done

if [[ "$FAIL" -ne 0 ]]; then
  echo "CHRONY_GATE=FAIL" > "$LOG_DIR/chrony_gate_result"
  exit 1
fi
echo "CHRONY_GATE=PASS" > "$LOG_DIR/chrony_gate_result"
