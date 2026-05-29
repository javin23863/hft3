#!/usr/bin/env bash
# One-time Rithmic paper trial setup on CHI404 (colo only — do not run on a workstation).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3}"
cd "$REPO"

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export RITHMIC_TRIAL_ENABLED="${RITHMIC_TRIAL_ENABLED:-1}"
export RITHMIC_TRIAL_CONNECTOR="${RITHMIC_TRIAL_CONNECTOR:-rtrader}"
export RITHMIC_TRIAL_CONFIG="${RITHMIC_TRIAL_CONFIG:-data_system/config/rithmic_trial.yaml}"
export RITHMIC_ENVIRONMENT="${RITHMIC_ENVIRONMENT:-Rithmic Paper Trading}"
export RITHMIC_GATEWAY="${RITHMIC_GATEWAY:-Chicago}"

echo "=== CHI404 Rithmic trial setup ==="
echo "Repo: $REPO"
echo "Config: $RITHMIC_TRIAL_CONFIG"

bash infrastructure/chi404/08_rtrader_wine_setup.sh

DISCOVERY="/root/hft3/logs/rtrader/rtrader_discovery.json"
if [[ -f "$DISCOVERY" ]]; then
  echo "Discovery written: $DISCOVERY"
  echo "Copy watch_dirs into data_system/config/rithmic_trial.yaml before live capture."
fi

echo ""
echo "Next steps (on CHI404):"
echo "  1. First login: bash /root/hft3/logs/rtrader/launch_rtrader.sh  (Paper / Chicago)"
echo "  2. Update rithmic_trial.yaml rtrader.watch_dirs from discovery JSON"
echo "  3. Set enabled: true in yaml OR export RITHMIC_TRIAL_ENABLED=1"
echo "  4. python -m data_system.rithmic_trial.pipeline run-unattended --config $RITHMIC_TRIAL_CONFIG"
