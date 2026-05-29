#!/bin/bash
# Install systemd unit for Rithmic trial unattended capture on CHI404.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO_DIR="${HFT3_REPO_DIR:-/root/hft3/repo}"
TRIAL_CONFIG="${RITHMIC_TRIAL_CONFIG:-data_system/config/rithmic_trial.yaml}"
LOG_DIR="/root/hft3/logs/rithmic_trial"
mkdir -p "$LOG_DIR"

cat > /etc/systemd/system/hft3-rithmic-trial.service << EOF
[Unit]
Description=HFT3 Rithmic trial unattended capture (CHI404)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/python3 -m data_system.rithmic_trial.pipeline run-unattended --config ${TRIAL_CONFIG}
Restart=on-failure
RestartSec=15
StandardOutput=append:${LOG_DIR}/systemd.log
StandardError=append:${LOG_DIR}/systemd.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Installed hft3-rithmic-trial.service (WorkingDirectory=${REPO_DIR})"
echo "Enable with: systemctl enable --now hft3-rithmic-trial"
