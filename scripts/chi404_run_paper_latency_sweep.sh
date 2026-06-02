#!/usr/bin/env bash
# CHI404 orchestrator: R|API+ reachability gate -> paper latency daemon -> R|API+ paper orders
#    -> reports -> latency_summary.
# Canonical path: docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md
#
# This orchestrator now uses the R|API+ connector (ctypes bridge to librithmic_gateway_shared.so)
# directly on CHI404 -- no Windows VM, no R|Trader GUI, no SMB watch_dir.
#
# KNOWN GAP (Session 2026-06-02, RAPI+ handoff): the R|API+ order events are not yet wired
# into the SPSC queue that the paper_latency_daemon polls. Until that wiring is in place
# the daemon's paired_submit_ack_count stays at 0 and this orchestrator will exit 1
# after 120*10s if --no-orders-burst is not set. Set PAPER_LATENCY_SKIP_ORDERS_BURST=1
# to run the daemon-only pass (verify reachability + connectivity, no order count).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"

# Reject any log-laundering helper that's still on disk.
if [[ -f scripts/chi404_reprocess_sweep_log.py ]]; then
  if ! grep -q 'moved to scripts/deprecated' scripts/chi404_reprocess_sweep_log.py 2>/dev/null; then
    echo "FAIL: chi404_reprocess_sweep_log.py is a log-laundering path -- remove or stub it" >&2
    exit 1
  fi
fi

TARGET="${PAPER_LATENCY_TARGET_ORDERS:-1000}"
RUN_ID="${PAPER_LATENCY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
TRIAL_CONFIG="${RITHMIC_TRIAL_CONFIG:-packages/data_system/config/rithmic_trial.yaml}"
export PAPER_LATENCY_RUN_ID="$RUN_ID"
SYMBOL="${PAPER_LATENCY_SYMBOL:-MES}"
EXCHANGE="${PAPER_LATENCY_EXCHANGE:-CME}"

# Hard precondition: the connector must be rithmic_api. Bail loud if a config
# still points at the deleted R|Trader VM bridge.
if [[ "${RITHMIC_TRIAL_CONNECTOR:-}" != "rithmic_api" ]]; then
  echo "FAIL: RITHMIC_TRIAL_CONNECTOR=${RITHMIC_TRIAL_CONNECTOR:-unset}, expected rithmic_api" >&2
  echo "      VM-coupled R|Trader bridge is removed. Update /root/hft3/.env." >&2
  exit 1
fi

# R|API+ reachability gate: probe the SDK + SSL + repo login once, then exit.
# Reuses the connector so we get the same RithmicApiError path the daemon sees.
echo "== R|API+ reachability gate =="
PYTHONPATH="$REPO/packages" HFT3_RITHMIC_GATEWAY_SO="${HFT3_RITHMIC_GATEWAY_SO:-$REPO/build/rithmic_gateway/librithmic_gateway_shared.so}" \
  python3 - <<'PY'
import os, sys
sys.path.insert(0, "/root/hft3/repo/packages")
from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector import build_connector
cfg = load_config(os.environ.get("TRIAL_CONFIG", "packages/data_system/config/rithmic_trial.yaml"))
c = build_connector(cfg)
c.connect()
lim = c.limitations()
print("OK", lim)
c.close()
PY

echo "== start paper latency daemon (rithmic_api connector) =="
PYTHONPATH="$REPO/packages" HFT3_RITHMIC_GATEWAY_SO="${HFT3_RITHMIC_GATEWAY_SO:-$REPO/build/rithmic_gateway/librithmic_gateway_shared.so}" \
  python3 -m data_system.rithmic_trial.pipeline paper-latency-daemon \
    --config "$TRIAL_CONFIG" \
    --run-id "$RUN_ID" &
DAEMON_PID=$!
sleep 2

cleanup() {
  kill "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup EXIT

if [[ "${PAPER_LATENCY_SKIP_ORDERS_BURST:-0}" == "1" ]]; then
  echo "== PAPER_LATENCY_SKIP_ORDERS_BURST=1 -> waiting briefly for daemon status then exiting =="
  sleep 15
  kill "$DAEMON_PID" 2>/dev/null || true
  wait "$DAEMON_PID" 2>/dev/null || true
  STATUS="$REPO/runtime/paper_latency/daemon_status.json"
  if [[ -f "$STATUS" ]]; then
    echo "== final status =="
    cat "$STATUS"
  fi
  echo "Done run_id=$RUN_ID (no-orders-burst pass; R|API+ order events wiring pending)"
  exit 0
fi

# TODO(2026-06-02): wire R|API+ order callbacks (orderSubmit, orderAck) into the
# SPSC queue that the paper_latency_daemon polls. Until then the daemon sees zero
# events and the paired count stays at 0.
echo "== send paper order burst (target=$TARGET symbol=$SYMBOL) =="
echo "   (NOT YET IMPLEMENTED for rithmic_api: see docs/RAPI_PLUS_HANDOFF_2026_06_02.md)"
echo "   set PAPER_LATENCY_SKIP_ORDERS_BURST=1 to skip this pass"

RECORDS="$REPO/runtime/paper_latency/raw/$RUN_ID/records.ndjson"
STATUS="$REPO/runtime/paper_latency/daemon_status.json"
echo "== wait for paired_submit_ack_count >= $TARGET (run_id=$RUN_ID) =="
PAIRED=0
for _ in $(seq 1 120); do
  if [[ -f "$STATUS" ]]; then
    PAIRED=$(python3 - <<PY
import json
from pathlib import Path
p = Path("$STATUS")
d = json.loads(p.read_text())
if d.get("run_id") != "$RUN_ID":
    print(0)
else:
    print(d.get("paired_submit_ack_count", 0))
PY
)
    echo "paired_submit_ack_count: $PAIRED"
    if [[ "$PAIRED" -ge "$TARGET" ]]; then
      break
    fi
  fi
  sleep 10
done
if [[ "$PAIRED" -lt "$TARGET" ]]; then
  echo "FAIL: paired_submit_ack_count=$PAIRED < $TARGET -- not promoting synthetic/stale logs" >&2
  exit 1
fi
if [[ ! -f "$RECORDS" ]]; then
  echo "FAIL: daemon records missing at $RECORDS" >&2
  exit 1
fi

DATE="$(date -u +%Y-%m-%d)"
REPORTS="$REPO/reports/rithmic_trial/$DATE"
echo "== promote daemon records to trial reports (min_paired=$TARGET) =="
PYTHONPATH="$REPO/packages" python3 -m data_system.rithmic_trial.latency.promote_reports \
  --records "$RECORDS" \
  --reports-dir "$REPORTS" \
  --min-paired "$TARGET"

echo "== waterfall report =="
python3 scripts/latency_probe/build_waterfall_report.py \
  --records "$RECORDS" \
  --out-md "$REPO/runtime/paper_latency/raw/$RUN_ID/waterfall.md"

PROBE_RUN="$(ls -1dt runtime/latency_reports/raw/* 2>/dev/null | head -1 | xargs basename 2>/dev/null || echo "")"
if [[ -n "$PROBE_RUN" ]]; then
  echo "== refresh latency_summary (probe run $PROBE_RUN) =="
  python3 scripts/latency_probe/summarize_latency.py \
    --run-id "$PROBE_RUN" \
    --include-trial-appendix
fi

echo "Done run_id=$RUN_ID records=$RECORDS paired=$PAIRED"
