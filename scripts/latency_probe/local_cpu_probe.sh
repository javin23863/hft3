#!/usr/bin/env bash
# CHI404 colo-only: CPU jitter (cyclictest) + loopback TCP. Run ON bare metal only.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
REPORT_ROOT="${LATENCY_REPORT_ROOT:-$REPO/runtime/latency_reports}"
RAW="$REPORT_ROOT/raw/$RUN_ID"
DURATION="${LATENCY_PROBE_CYCLICTEST_SEC:-120}"
HOT_CPUS="${HOT_CPUS:-2-11}"
CRITERIA="$REPO/infrastructure/chi404/PASS_CRITERIA.json"
J_MAX=$(python3 -c "import json; print(json.load(open('$CRITERIA'))['cyclictest_p99_max_us'])")

mkdir -p "$RAW"

FIRST=$(echo "$HOT_CPUS" | cut -d- -f1)
LAST=$(echo "$HOT_CPUS" | cut -d- -f2)
CPUS="$FIRST"
[[ "$FIRST" != "$LAST" ]] && CPUS="$FIRST $LAST"

echo "local_cpu_probe RUN_ID=$RUN_ID DURATION=${DURATION}s RAW=$RAW"

run_cyclictest() {
  local mode="$1"
  local cpu="$2"
  local outdir="$RAW/cyclictest_${mode}_cpu${cpu}"
  mkdir -p "$outdir"
  local hist="$outdir/latency.hist"
  local log="$outdir/run.log"

  if [[ "$mode" == "loaded" ]]; then
    stress-ng --cpu 4 --cpu-method matrixprod --timeout "$((DURATION + 30))s" >/dev/null 2>&1 &
    spid=$!
    cleanup_stress() { kill "$spid" 2>/dev/null || true; wait "$spid" 2>/dev/null || true; }
    trap cleanup_stress EXIT
    sleep 2
  fi

  echo "cyclictest mode=$mode cpu=$cpu duration=${DURATION}s limit_p99=${J_MAX}us" | tee "$log"
  taskset -c "$cpu" cyclictest -p 95 -t1 -i 1000 -D "${DURATION}s" -a "$cpu" --policy=fifo \
    -h 400 --histofall=99999999 --histfile="$hist" -q 2>&1 | tee -a "$log"

  if [[ ! -s "$hist" ]]; then
    echo "FAIL cyclictest mode=$mode cpu=$cpu: empty histogram $hist" >&2
    exit 1
  fi

  python3 "$REPO/scripts/latency_probe/hist_utils.py" "$hist" > "$outdir/percentiles.json"
  samples=$(python3 -c "import json; print(json.load(open('$outdir/percentiles.json'))['samples'])")
  if [[ "$samples" -eq 0 ]]; then
    echo "FAIL cyclictest mode=$mode cpu=$cpu: samples=0" >&2
    exit 1
  fi

  if [[ "$mode" == "loaded" ]]; then
    trap - EXIT
    cleanup_stress
  fi
}

for cpu in $CPUS; do
  run_cyclictest idle "$cpu"
  run_cyclictest loaded "$cpu"
done

python3 - "$RAW/loopback_tcp.json" <<'PY'
import json
import socket
import statistics
import sys
import time

out = sys.argv[1]
samples = []
errors = []
for _ in range(100):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        t0 = time.perf_counter()
        c = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        conn, _ = s.accept()
        samples.append((time.perf_counter() - t0) * 1_000_000)
        c.close()
        conn.close()
    except OSError as exc:
        errors.append(str(exc))
    finally:
        s.close()

def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    idx = min(len(vals) - 1, max(0, int(p * len(vals)) - 1))
    return vals[idx]

payload = {
    "samples": len(samples),
    "p50_us": pct(samples, 0.50),
    "p95_us": pct(samples, 0.95),
    "p99_us": pct(samples, 0.99),
    "p999_us": pct(samples, 0.999),
    "max_us": max(samples) if samples else None,
    "avg_us": statistics.mean(samples) if samples else None,
    "errors": errors[:3],
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
print(f"Wrote {out}")
PY

echo "$RUN_ID" > "$REPORT_ROOT/raw/LATEST_RUN_ID"
echo "local_cpu_probe done RUN_ID=$RUN_ID"
