#!/usr/bin/env bash
# Trial capture -> process -> replay on CHI404 (no synthetic writes, no VM).
# Replaces the previous VM-coupled variant. Uses the R|API+ connector directly.
set -euo pipefail
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"
DATE=$(date -u +%F)
EVENT_ID="${EVENT_ID:-}"
source /root/hft3/.env

if [[ "${RITHMIC_TRIAL_CONNECTOR:-}" != "rithmic_api" ]]; then
  echo "FAIL: RITHMIC_TRIAL_CONNECTOR=${RITHMIC_TRIAL_CONNECTOR:-unset}, expected rithmic_api" >&2
  exit 1
fi

# R|API+ is already running as a systemd daemon (hft3-rithmic-trial.service).
# Verify it's active before we kick off the capture/process/replay pipeline.
if ! systemctl is-active --quiet hft3-rithmic-trial.service; then
  echo "FAIL: hft3-rithmic-trial.service is not active. Start it first." >&2
  exit 1
fi

CAPTURE_ARGS=(--config packages/data_system/config/rithmic_trial.yaml --force --duration-sec 30 --date "$DATE" --symbol MES)
if [ -n "$EVENT_ID" ]; then
  CAPTURE_ARGS+=(--event-id "$EVENT_ID")
fi

echo "=== capture (folder date=$DATE event_id=${EVENT_ID:-none}) ==="
PYTHONPATH="$REPO/packages" HFT3_RITHMIC_GATEWAY_SO="${HFT3_RITHMIC_GATEWAY_SO:-$REPO/build/rithmic_gateway/librithmic_gateway_shared.so}" \
  python3 -m data_system.rithmic_trial.pipeline capture "${CAPTURE_ARGS[@]}"

echo "=== process ==="
PYTHONPATH="$REPO/packages" python3 -m data_system.rithmic_trial.pipeline process \
  --config packages/data_system/config/rithmic_trial.yaml \
  --date "$DATE" --symbol MES

if [ -n "$EVENT_ID" ]; then
  echo "=== replay-event ($EVENT_ID via Databento NPZ + CHI404 latency) ==="
  PYTHONPATH="$REPO/packages" python3 -m data_system.rithmic_trial.pipeline replay-event \
    --config packages/data_system/config/rithmic_trial.yaml \
    --event-id "$EVENT_ID"
else
  NPZ="$REPO/data/replay/hftbacktest/rithmic_trial/${DATE}/MES/MES_${DATE}_trial.npz"
  echo "WARNING: EVENT_ID unset -- replay-sample on trial NPZ is smoke only, not macro research." >&2
  echo "         Set EVENT_ID=CPI_2024_09_11_TIGHT for canonical replay (docs/vault/RESEARCH_ENTRYPOINTS.md)." >&2
  echo "=== replay-sample (trial NPZ, no event_id) ==="
  PYTHONPATH="$REPO/packages" python3 -m data_system.rithmic_trial.pipeline replay-sample --npz "$NPZ" --simple
  ls -la "$NPZ"
fi
