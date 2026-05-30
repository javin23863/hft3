#!/usr/bin/env bash
# CHI404 orchestrator: live gate → paper latency daemon → VM UI orders → reports → latency_summary.
# Canonical path: docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"

if grep -q 'Add-Content' scripts/chi404_vm_paper_order_sweep.ps1 2>/dev/null; then
  echo "FAIL: chi404_vm_paper_order_sweep.ps1 contains Add-Content (synthetic inject forbidden)" >&2
  exit 1
fi

if [[ -f scripts/chi404_reprocess_sweep_log.py ]]; then
  if ! grep -q 'moved to scripts/deprecated' scripts/chi404_reprocess_sweep_log.py 2>/dev/null; then
    echo "FAIL: chi404_reprocess_sweep_log.py is a log-laundering path — remove or stub it" >&2
    exit 1
  fi
fi

TARGET="${PAPER_LATENCY_TARGET_ORDERS:-1000}"
RUN_ID="${PAPER_LATENCY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
TRIAL_CONFIG="${RITHMIC_TRIAL_CONFIG:-data_system/config/rithmic_trial.yaml}"
export PAPER_LATENCY_RUN_ID="$RUN_ID"

echo "== live gate =="
bash scripts/chi404_vm_live_gate.sh

echo "== start paper latency daemon =="
python3 -m data_system.rithmic_trial.pipeline paper-latency-daemon \
  --config "$TRIAL_CONFIG" \
  --run-id "$RUN_ID" &
DAEMON_PID=$!
sleep 2

cleanup() {
  kill "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "== trigger VM paper order sweep (target=$TARGET) =="
if [[ ! -f scripts/chi404_trigger_vm_paper_sweep.py ]]; then
  echo "FAIL: missing scripts/chi404_trigger_vm_paper_sweep.py" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source /root/hft3/.env 2>/dev/null || true
set +a
export PAPER_LATENCY_TARGET_ORDERS="$TARGET"
python3 scripts/chi404_trigger_vm_paper_sweep.py

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
  echo "FAIL: paired_submit_ack_count=$PAIRED < $TARGET — not promoting synthetic/stale logs" >&2
  exit 1
fi
if [[ ! -f "$RECORDS" ]]; then
  echo "FAIL: daemon records missing at $RECORDS" >&2
  exit 1
fi

DATE="$(date -u +%Y-%m-%d)"
REPORTS="$REPO/reports/rithmic_trial/$DATE"
echo "== promote daemon records to trial reports (min_paired=$TARGET) =="
python3 -m data_system.rithmic_trial.latency.promote_reports \
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
