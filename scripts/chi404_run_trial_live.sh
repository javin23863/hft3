#!/usr/bin/env bash
# Live trial capture -> process -> replay on CHI404 (no synthetic writes).
set -euo pipefail
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"
DATE=$(date -u +%F)
EVENT_ID="${EVENT_ID:-}"
source /root/hft3/.env

export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rtrader
export RTRADER_WATCH_DIRS="${RTRADER_WATCH_DIRS:-/root/hft3/rtrader_watch}"

echo "=== live preflight gate ==="
bash "$REPO/scripts/chi404_vm_live_gate.sh"

CAPTURE_ARGS=(--config data_system/config/rithmic_trial.yaml --force --duration-sec 30 --date "$DATE" --symbol MES)
if [ -n "$EVENT_ID" ]; then
  CAPTURE_ARGS+=(--event-id "$EVENT_ID")
fi

echo "=== capture (folder date=$DATE event_id=${EVENT_ID:-none}) ==="
python3 -m data_system.rithmic_trial.pipeline capture "${CAPTURE_ARGS[@]}"

echo "=== process ==="
python3 -m data_system.rithmic_trial.pipeline process \
  --config data_system/config/rithmic_trial.yaml \
  --date "$DATE" --symbol MES

if [ -n "$EVENT_ID" ]; then
  echo "=== replay-event ($EVENT_ID via Databento NPZ + CHI404 latency) ==="
  python3 -m data_system.rithmic_trial.pipeline replay-event \
    --config data_system/config/rithmic_trial.yaml \
    --event-id "$EVENT_ID"
else
  NPZ="$REPO/data/replay/hftbacktest/rithmic_trial/${DATE}/MES/MES_${DATE}_trial.npz"
  echo "WARNING: EVENT_ID unset — replay-sample on trial NPZ is smoke only, not macro research." >&2
  echo "         Set EVENT_ID to any id in packages/data_system/config/events.csv (python packages/data_system/src/macro_event_cli.py)." >&2
  echo "=== replay-sample (trial NPZ, no event_id) ==="
  python3 -m data_system.rithmic_trial.pipeline replay-sample --npz "$NPZ" --simple
  ls -la "$NPZ"
fi
