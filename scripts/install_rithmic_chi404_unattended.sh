#!/usr/bin/env bash
# Full CHI404 install: deps, R|API+ service env, systemd.
# Run on CHI404 after repo sync and .env deploy (colo only).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"

cd "$REPO"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export HFT3_REPO_DIR="$REPO"
export RITHMIC_TRIAL_ENABLED="${RITHMIC_TRIAL_ENABLED:-1}"
export RITHMIC_TRIAL_CONNECTOR="${RITHMIC_TRIAL_CONNECTOR:-rithmic_api}"

echo "=== HFT3 Rithmic trial unattended install ==="
echo "Repo: $REPO"

echo "--- Python dependencies for orchestration/capture only ---"
python3 -m pip install -q -r packages/data_system/requirements.txt

echo "--- Native C++ R|API+ probe build ---"
cmake --build build --target rithmic_latency_probe --config Release

echo "--- systemd unit ---"
bash infrastructure/chi404/09_rithmic_trial_systemd.sh
systemctl enable hft3-rithmic-trial

echo ""
echo "Install complete."
echo "  1. Start capture: systemctl start hft3-rithmic-trial"
echo "  2. Run native latency: ./build/rithmic_gateway/rithmic_latency_probe"
echo "  3. Status: systemctl status hft3-rithmic-trial"
echo "  4. Log: tail -f ${REPO}/logs/rithmic_trial/unattended.log"
