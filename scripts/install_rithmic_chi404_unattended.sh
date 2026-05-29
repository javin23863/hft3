#!/usr/bin/env bash
# Full CHI404 install: deps, Wine/R|Trader setup, watch_dirs env, systemd.
# Run on CHI404 after repo sync and .env deploy (colo only).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
DISCOVERY="/root/hft3/logs/rtrader/rtrader_discovery.json"

cd "$REPO"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export HFT3_REPO_DIR="$REPO"
export RITHMIC_TRIAL_ENABLED="${RITHMIC_TRIAL_ENABLED:-1}"
export RITHMIC_TRIAL_CONNECTOR="${RITHMIC_TRIAL_CONNECTOR:-rtrader}"

echo "=== HFT3 Rithmic trial unattended install ==="
echo "Repo: $REPO"

echo "--- Python dependencies ---"
python3 -m pip install -q -r data_system/requirements.txt

echo "--- Wine + R|Trader setup ---"
bash scripts/setup_rithmic_chi404.sh

if [[ -f "$DISCOVERY" ]]; then
  echo "--- Writing RTRADER_WATCH_DIRS from discovery ---"
  WATCH=$(
    python3 -c "
import json
from pathlib import Path
data = json.loads(Path('${DISCOVERY}').read_text(encoding='utf-8'))
print(';'.join(data.get('watch_dirs', [])))
"
  )
  if [[ -n "$WATCH" ]]; then
    if grep -q '^RTRADER_WATCH_DIRS=' "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^RTRADER_WATCH_DIRS=.*|RTRADER_WATCH_DIRS=${WATCH}|" "$ENV_FILE"
    else
      echo "RTRADER_WATCH_DIRS=${WATCH}" >> "$ENV_FILE"
    fi
    echo "RTRADER_WATCH_DIRS set (${#WATCH} chars)"
  else
    echo "WARNING: discovery watch_dirs empty — complete R|Trader install/login first"
  fi
else
  echo "WARNING: $DISCOVERY not found — run wine setup manually"
fi

echo "--- systemd unit ---"
bash infrastructure/chi404/09_rithmic_trial_systemd.sh
systemctl enable hft3-rithmic-trial

echo ""
echo "Install complete."
echo "  1. First login (if not done): bash /root/hft3/logs/rtrader/launch_rtrader.sh"
echo "  2. Start capture: systemctl start hft3-rithmic-trial"
echo "  3. Status: systemctl status hft3-rithmic-trial"
echo "  4. Log: tail -f ${REPO}/logs/rithmic_trial/unattended.log"
