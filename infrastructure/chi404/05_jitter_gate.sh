#!/bin/bash
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
CRITERIA="${HFT3_REPO_DIR:-/root/hft3}/infrastructure/chi404/PASS_CRITERIA.json"
[[ -f /root/hft3/repo/infrastructure/chi404/PASS_CRITERIA.json ]] && CRITERIA=/root/hft3/repo/infrastructure/chi404/PASS_CRITERIA.json
[[ -f /root/hft3/infrastructure/chi404/PASS_CRITERIA.json ]] && CRITERIA=/root/hft3/infrastructure/chi404/PASS_CRITERIA.json

J_MAX=$(python3 -c "import json; print(json.load(open('$CRITERIA'))['cyclictest_p99_max_us'])")
HOT_CPUS="${HOT_CPUS:-2-11}"

mkdir -p "$LOG_DIR"
: > "$LOG_DIR/jitter_gate.txt"
FAIL=0

FIRST=$(echo "$HOT_CPUS" | cut -d- -f1)
LAST=$(echo "$HOT_CPUS" | cut -d- -f2)
CPUS_TO_TEST="$FIRST"
[[ "$FIRST" != "$LAST" ]] && CPUS_TO_TEST="$FIRST $LAST"

stress-ng --cpu 4 --cpu-method matrixprod --timeout 360s >/dev/null 2>&1 &
STRESS_PID=$!
sleep 2

parse_p99_us() {
  local histfile="$1"
  python3 - "$histfile" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("9999")
    sys.exit(0)

total = 0
buckets = []
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split()
    if len(parts) < 2:
        continue
    try:
        usec = int(parts[0])
        count = int(parts[1])
    except ValueError:
        continue
    buckets.append((usec, count))
    total += count

if total == 0:
    print("9999")
    sys.exit(0)

target = int(total * 0.99)
running = 0
p99 = buckets[-1][0]
for usec, count in buckets:
    running += count
    if running >= target:
        p99 = usec
        break
print(p99)
PY
}

for cpu in $CPUS_TO_TEST; do
  OUT="$LOG_DIR/cyclictest_cpu${cpu}.txt"
  HIST="$LOG_DIR/cyclictest_cpu${cpu}.hist"
  echo "cyclictest on CPU $cpu (p99 max ${J_MAX} us, ~5 min)" | tee "$OUT"
  taskset -c "$cpu" cyclictest -p 95 -t1 -i 1000 -D 300s -a "$cpu" --policy=fifo \
    -h 400 --histofall=99999999 --histfile="$HIST" -q 2>&1 | tee -a "$OUT"
  P99=$(parse_p99_us "$HIST")
  echo "$P99" > "$LOG_DIR/cyclictest_cpu${cpu}_p99_us"
  echo "cpu $cpu p99_latency_us=$P99 limit=$J_MAX" | tee -a "$OUT"
  if [[ "$P99" -gt "$J_MAX" ]]; then
    echo "FAIL cpu $cpu p99 ${P99}us > ${J_MAX}us" | tee -a "$LOG_DIR/jitter_gate.txt"
    FAIL=1
  else
    echo "PASS cpu $cpu p99 ${P99}us" | tee -a "$LOG_DIR/jitter_gate.txt"
  fi
done

kill "$STRESS_PID" 2>/dev/null || true
wait "$STRESS_PID" 2>/dev/null || true

if [[ "$FAIL" -ne 0 ]]; then
  echo "JITTER_GATE=FAIL" > "$LOG_DIR/jitter_gate_result"
  exit 1
fi
echo "JITTER_GATE=PASS" > "$LOG_DIR/jitter_gate_result"
echo "Jitter gate PASS"
