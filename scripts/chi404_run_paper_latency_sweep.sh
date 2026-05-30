#!/usr/bin/env bash
# CHI404 orchestrator: live gate → paper latency daemon → VM sweep → reports → latency_summary.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"

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
if command -v virsh >/dev/null 2>&1; then
  virsh qemu-agent-command rtrader-win --cmd \
    '{"execute":"guest-exec","arguments":{"path":"powershell.exe","arg":["-File","C:\\hft3\\scripts\\chi404_vm_paper_order_sweep.ps1","-TargetOrders","'"$TARGET"'"]}}' \
    2>/dev/null || echo "WARN: virsh guest-exec failed — run sweep manually in VM"
else
  echo "WARN: virsh not found — run scripts/chi404_vm_paper_order_sweep.ps1 inside R|Trader VM"
fi

RECORDS="$REPO/runtime/paper_latency/raw/$RUN_ID/records.ndjson"
STATUS="$REPO/runtime/paper_latency/daemon_status.json"
echo "== wait for paired_submit_ack_count >= $TARGET =="
for _ in $(seq 1 120); do
  if [[ -f "$STATUS" ]]; then
    PAIRED=$(python3 - <<PY
import json
from pathlib import Path
p = Path("$STATUS")
print(json.loads(p.read_text()).get("paired_submit_ack_count", 0))
PY
)
    echo "paired_submit_ack_count: $PAIRED"
    if [[ "$PAIRED" -ge "$TARGET" ]]; then
      break
    fi
  fi
  sleep 10
done

DATE="$(date -u +%Y-%m-%d)"
REPORTS="$REPO/reports/rithmic_trial/$DATE"
echo "== promote daemon records to trial reports =="
python3 -m data_system.rithmic_trial.latency.promote_reports \
  --records "$RECORDS" \
  --reports-dir "$REPORTS"

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

echo "Done run_id=$RUN_ID records=$RECORDS"
