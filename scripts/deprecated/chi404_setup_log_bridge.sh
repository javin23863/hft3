#!/bin/bash
# Configure CHI404 capture to read pushed R|Trader logs (no Wine, no R|API).
set -euo pipefail

WATCH="/root/hft3/rtrader_watch"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"

mkdir -p "$WATCH"

# Stop Wine/dotnet background noise
systemctl stop hft3-rithmic-trial 2>/dev/null || true
killall -9 wine wineserver winetricks 2>/dev/null || true
pkill -9 -f dotnet472 2>/dev/null || true
pkill -9 -f chi404_setup_wine32 2>/dev/null || true

python3 <<PY
import json
from pathlib import Path
watch = ["$WATCH"]
Path("/root/hft3/logs/rtrader/rtrader_discovery.json").write_text(
    json.dumps({"watch_dirs": watch, "bridge": "log_push"}, indent=2) + "\n", encoding="utf-8"
)
print("watch_dirs:", watch)
PY

if grep -q '^RTRADER_WATCH_DIRS=' "$ENV_FILE" 2>/dev/null; then
  sed -i "s|^RTRADER_WATCH_DIRS=.*|RTRADER_WATCH_DIRS=\"${WATCH}\"|" "$ENV_FILE"
else
  echo "RTRADER_WATCH_DIRS=\"${WATCH}\"" >> "$ENV_FILE"
fi

grep -q '^RTRADER_START_WINE=' "$ENV_FILE" 2>/dev/null && \
  sed -i 's|^RTRADER_START_WINE=.*|RTRADER_START_WINE=0|' "$ENV_FILE" || \
  echo "RTRADER_START_WINE=0" >> "$ENV_FILE"

grep -q '^RITHMIC_TRIAL_CONNECTOR=' "$ENV_FILE" 2>/dev/null && \
  sed -i 's|^RITHMIC_TRIAL_CONNECTOR=.*|RITHMIC_TRIAL_CONNECTOR=rtrader|' "$ENV_FILE" || \
  echo "RITHMIC_TRIAL_CONNECTOR=rtrader" >> "$ENV_FILE"

grep -q '^RITHMIC_TRIAL_ENABLED=' "$ENV_FILE" 2>/dev/null && \
  sed -i 's|^RITHMIC_TRIAL_ENABLED=.*|RITHMIC_TRIAL_ENABLED=1|' "$ENV_FILE" || \
  echo "RITHMIC_TRIAL_ENABLED=1" >> "$ENV_FILE"

systemctl enable hft3-rithmic-trial 2>/dev/null || bash "$REPO/infrastructure/chi404/09_rithmic_trial_systemd.sh"
systemctl restart hft3-rithmic-trial

echo "Log bridge ready: $WATCH"
echo "Start on workstation: pwsh scripts/push_rtrader_logs_chi404.ps1"
