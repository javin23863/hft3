#!/bin/bash
# Install systemd unit for HFT3 binary market-data capture daemon on CHI404.
#
# Creates:
#   /etc/systemd/system/hft3-capture.service
#   /root/hft3/logs/capture/          (log directory)
#   /root/hft3/data/capture/          (output root for .cap files and manifests)
#
# Usage:
#   bash infrastructure/chi404/20_capture_daemon_systemd.sh
#
# Credentials must be in the EnvironmentFile (/root/hft3/.env).
# This script never reads, prints, or logs credential values.

set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO_DIR="${HFT3_REPO_DIR:-/root/hft3/repo}"
CAPTURE_CONFIG="${CAPTURE_CONFIG:-${REPO_DIR}/configs/capture/capture_chi404.yaml}"
LOG_DIR="/root/hft3/logs/capture"
DATA_DIR="/root/hft3/data/capture"

mkdir -p "$LOG_DIR"
mkdir -p "$DATA_DIR"

cat > /etc/systemd/system/hft3-capture.service << EOF
[Unit]
Description=HFT3 binary market-data capture daemon (CHI404, CC2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${REPO_DIR}/build/rithmic_gateway/capture_daemon ${CAPTURE_CONFIG}
Restart=always
RestartSec=15
StandardOutput=append:${LOG_DIR}/capture.log
StandardError=append:${LOG_DIR}/capture.log

# CPU/memory resource controls.
# Cores 2-11 are reserved for HFT workloads on CHI404.
CPUAffinity=2-11
CPUWeight=1000
MemoryHigh=4G
MemoryMax=6G

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Installed hft3-capture.service (WorkingDirectory=${REPO_DIR})"
echo "  config:   ${CAPTURE_CONFIG}"
echo "  log:      ${LOG_DIR}/capture.log"
echo "  data:     ${DATA_DIR}"
echo "Enable with: systemctl enable --now hft3-capture"
