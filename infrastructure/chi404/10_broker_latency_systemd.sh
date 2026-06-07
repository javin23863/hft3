#!/bin/bash
# Install systemd unit for CHI404 broker order latency daemon.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO_DIR="${HFT3_REPO_DIR:-/root/hft3/repo}"
TRIAL_CONFIG="${RITHMIC_TRIAL_CONFIG:-packages/data_system/config/rithmic_trial.yaml}"
LOG_DIR="/root/hft3/logs/broker_latency"
mkdir -p "$LOG_DIR"

cat > /etc/systemd/system/hft3-broker-latency.service << EOF
[Unit]
Description=HFT3 broker order latency waterfall daemon (CHI404)
After=network-online.target hft3-rithmic-trial.service
Wants=network-online.target
Requires=hft3-rithmic-trial.service

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONPATH=${REPO_DIR}/packages:${REPO_DIR}/apps:${REPO_DIR}
ExecStart=/usr/bin/python3 -m data_system.rithmic_trial.pipeline broker-latency-daemon --config ${TRIAL_CONFIG}
Restart=on-failure
RestartSec=15
StandardOutput=append:${LOG_DIR}/systemd.log
StandardError=append:${LOG_DIR}/systemd.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Installed hft3-broker-latency.service (WorkingDirectory=${REPO_DIR})"
echo "Enable with: systemctl enable --now hft3-broker-latency"
