#!/bin/bash
# Install systemd oneshot to re-apply IRQ/net tuning after reboot (ethtool not persistent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"

cat > /etc/systemd/system/hft3-net-tune.service << EOF
[Unit]
Description=HFT3 IRQ and NIC tuning (CHI404)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=HFT3_ENV_FILE=${ENV_FILE}
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash ${SCRIPT_DIR}/04_irq_net_tuning.sh

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hft3-net-tune.service
echo "Enabled hft3-net-tune.service (runs 04_irq_net_tuning.sh on boot)"
