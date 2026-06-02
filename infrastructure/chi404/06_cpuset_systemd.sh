#!/bin/bash
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

HOT_CPUS="${HOT_CPUS:-2-11}"
RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"

# cgroup v2 cpuset for future hft3 services
CG_ROOT="/sys/fs/cgroup"
if [[ -f "$CG_ROOT/cgroup.controllers" ]]; then
  mkdir -p "$CG_ROOT/hft3-hot"
  echo "$HOT_CPUS" > "$CG_ROOT/hft3-hot/cpuset.cpus" 2>/dev/null || echo "+cpuset" > "$CG_ROOT/cgroup.subtree_control" && echo "$HOT_CPUS" > "$CG_ROOT/hft3-hot/cpuset.cpus"
  echo 0 > "$CG_ROOT/hft3-hot/cpuset.mems" 2>/dev/null || true
  echo "cpuset hft3-hot cpus=$HOT_CPUS" | tee "$LOG_DIR/cpuset.txt"
fi

# Persistent systemd oneshot: re-create the cpuset cgroup on every boot
# (cgroup v2 children don't survive reboot by default). Read HOT_CPUS from
# the env file at boot time.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cat > /etc/systemd/system/hft3-cpuset.service << EOF
[Unit]
Description=HFT3 cpuset cgroup (CHI404)
After=systemd-tmpfiles-setup.service
Before=hft3-rithmic-trial.service
ConditionPathExists=/sys/fs/cgroup/cgroup.controllers

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash -c 'CG=/sys/fs/cgroup/hft3-hot; mkdir -p \$\$CG; echo \$\${HOT_CPUS:-2-11} > \$\$CG/cpuset.cpus 2>/dev/null || (echo +cpuset > /sys/fs/cgroup/cgroup.subtree_control && echo \$\${HOT_CPUS:-2-11} > \$\$CG/cpuset.cpus); echo 0 > \$\$CG/cpuset.mems 2>/dev/null || true; echo hft3-hot cpuset: cpus=\$\${HOT_CPUS:-2-11} mems=0'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hft3-cpuset.service 2>/dev/null || true
echo "systemd unit hft3-cpuset.service enabled (persistent cpuset cgroup)" | tee -a "$LOG_DIR/cpuset.txt"

mkdir -p /etc/systemd/system/hft3-gateway.service.d
cat > /etc/systemd/system/hft3-gateway.service << 'EOF'
[Unit]
Description=HFT3 Rithmic Gateway (stub)
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
CPUAffinity=1

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/systemd/system/hft3-engine.service.d
cat > /etc/systemd/system/hft3-engine.service << EOF
[Unit]
Description=HFT3 Decision Engine (stub)
After=hft3-gateway.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
CPUAffinity=${HOT_CPUS}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "systemd units hft3-gateway (cpu 1) and hft3-engine (cpus $HOT_CPUS) stubbed" | tee -a "$LOG_DIR/cpuset.txt"
