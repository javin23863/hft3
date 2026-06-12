#!/usr/bin/env bash
# Canonical M5 order-ack campaign entrypoint (LATENCY.md §9, ALPHA_CME M5).
# Authoritative measurement = native C++ probe (rithmic_latency_probe), no Python on the timing path.
# hot_path_language=c++, wrapper=none
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "chi404_run_paper_latency_sweep: Linux CHI404 bare metal only" >&2
  exit 1
fi

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"

# Campaign knobs
PAPER_ORDER_TARGET="${PAPER_ORDER_TARGET:-1000}"
BATCH="${BATCH:-200}"
MAX_BATCHES="${MAX_BATCHES:-15}"

# Probe env defaults
RITHMIC_PROBE_SYMBOL="${RITHMIC_PROBE_SYMBOL:-MESM6}"
RITHMIC_PROBE_EXCHANGE="${RITHMIC_PROBE_EXCHANGE:-CME}"
RITHMIC_PROBE_ORDER_INTERVAL_US="${RITHMIC_PROBE_ORDER_INTERVAL_US:-250000}"
RITHMIC_PROBE_CANCEL_AFTER_ACK="${RITHMIC_PROBE_CANCEL_AFTER_ACK:-1}"
RITHMIC_PROBE_CPU="${RITHMIC_PROBE_CPU:--1}"
RITHMIC_PROBE_RT_PRIORITY="${RITHMIC_PROBE_RT_PRIORITY:-0}"
RITHMIC_PROBE_MLOCK="${RITHMIC_PROBE_MLOCK:-1}"
RITHMIC_PROBE_SKIP_MD="${RITHMIC_PROBE_SKIP_MD:-}"
RITHMIC_PROBE_REQUIRE_MD="${RITHMIC_PROBE_REQUIRE_MD:-}"
RITHMIC_PROBE_ORDER_PRICE="${RITHMIC_PROBE_ORDER_PRICE:-}"
RITHMIC_PROBE_PREFAULT_BYTES="${RITHMIC_PROBE_PREFAULT_BYTES:-}"

echo "chi404_run_paper_latency_sweep: REPO=$REPO TARGET=$PAPER_ORDER_TARGET BATCH=$BATCH MAX_BATCHES=$MAX_BATCHES"

# Build native C++ probe
cmake --build "$REPO/build" --target rithmic_latency_probe --config Release

BASELINE_DIR="$REPO/data/latency_baselines/$(date -u +%F)"
mkdir -p "$BASELINE_DIR"

batch_n=0
paired=0

while (( batch_n < MAX_BATCHES )) && (( paired < PAPER_ORDER_TARGET )); do
  batch_n=$(( batch_n + 1 ))
  RUN_ID="order_ack_campaign_$(date -u +%Y%m%dT%H%M%SZ)_${batch_n}"
  echo "--- batch ${batch_n}/${MAX_BATCHES}  RUN_ID=$RUN_ID ---"

  (
    export RITHMIC_PROBE_RUN_ID="$RUN_ID"
    export RITHMIC_PROBE_ORDER_COUNT="$BATCH"
    export RITHMIC_PROBE_SYMBOL
    export RITHMIC_PROBE_EXCHANGE
    export RITHMIC_PROBE_ORDER_INTERVAL_US
    export RITHMIC_PROBE_CANCEL_AFTER_ACK
    export RITHMIC_PROBE_CPU
    export RITHMIC_PROBE_RT_PRIORITY
    export RITHMIC_PROBE_MLOCK
    [[ -n "$RITHMIC_PROBE_SKIP_MD" ]]        && export RITHMIC_PROBE_SKIP_MD
    [[ -n "$RITHMIC_PROBE_REQUIRE_MD" ]]     && export RITHMIC_PROBE_REQUIRE_MD
    [[ -n "$RITHMIC_PROBE_ORDER_PRICE" ]]    && export RITHMIC_PROBE_ORDER_PRICE
    [[ -n "$RITHMIC_PROBE_PREFAULT_BYTES" ]] && export RITHMIC_PROBE_PREFAULT_BYTES
    cd "$REPO"
    ./build/rithmic_gateway/rithmic_latency_probe
  ) || true

  # Count cumulative successful paired order-ack records across today's campaign files
  paired=$(python3 - "$BASELINE_DIR" <<'PYEOF'
import sys, json, pathlib, os
d = pathlib.Path(sys.argv[1])
n = 0
for f in d.glob("order_ack_campaign_*.jsonl"):
    for line in f.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("order_action") == "new"
                and r.get("success") is True
                and r.get("send_to_ack_us") is not None):
            n += 1
print(n)
PYEOF
)
  [[ "$paired" =~ ^[0-9]+$ ]] || { echo "FAIL: paired count parse error: $paired" >&2; exit 1; }

  echo "paired ${paired}/${PAPER_ORDER_TARGET}"
done

if (( paired < PAPER_ORDER_TARGET )); then
  echo "FAIL: campaign exhausted MAX_BATCHES=$MAX_BATCHES with only paired=$paired < target=$PAPER_ORDER_TARGET" >&2
  echo "FAIL: check RITHMIC_ENDPOINT_PROFILE, connectivity, and probe logs in $BASELINE_DIR" >&2
  exit 1
fi

echo "Target reached (paired=$paired). Regenerating latency_summary.json via run_all..."
bash "$REPO/scripts/latency_probe/run_all.sh" || true

SUMMARY="$REPO/runtime/latency_reports/latency_summary.json"

python3 - "$SUMMARY" "$PAPER_ORDER_TARGET" <<PYEOF
import sys, json, pathlib

path = pathlib.Path(sys.argv[1])
target = int(sys.argv[2])

if not path.is_file():
    print(f"FAIL: latency_summary.json not found at {path}", file=sys.stderr)
    sys.exit(1)

try:
    s = json.loads(path.read_text())
except Exception as e:
    print(f"FAIL: cannot parse latency_summary.json: {e}", file=sys.stderr)
    sys.exit(1)

pol = s.get("paper_order_latency", {})
if pol.get("measured") is not True:
    print("FAIL: latency_summary.json paper_order_latency.measured != true", file=sys.stderr)
    sys.exit(1)

pc = pol.get("paired_count", 0)
if pc < target:
    print(f"FAIL: latency_summary.json paired_count={pc} < target={target}", file=sys.stderr)
    sys.exit(1)

p99 = s.get("order_ack_p99_ms")
try:
    float(p99)
except (TypeError, ValueError):
    print(f"FAIL: latency_summary.json order_ack_p99_ms is not numeric: {p99!r}", file=sys.stderr)
    sys.exit(1)

print(f"SUMMARY OK  paired_count={pc}  order_ack_p99_ms={p99}")
PYEOF

SUMMARY_JSON="$REPO/runtime/latency_reports/latency_summary.json"
p99_ms=$(python3 -c "import sys,json,pathlib; s=json.loads(pathlib.Path(sys.argv[1]).read_text()); print(s['order_ack_p99_ms'])" "$SUMMARY_JSON")

echo "=== chi404_run_paper_latency_sweep complete ==="
echo "  paired_count : ${paired}"
echo "  order_ack_p99_ms : ${p99_ms}"
echo "  Authority: hot_path_language=c++, wrapper=none."
