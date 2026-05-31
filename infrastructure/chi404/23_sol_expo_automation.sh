#!/bin/bash
# Attempt EXPO enable via IPMI SOL keyboard automation (AMI BIOS).
set -euo pipefail
source /root/hft3/.env
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

apt-get install -y expect >/dev/null 2>&1 || true

# Wait for host to enter BIOS (post-reboot)
sleep 90

export HFT3_BMC_IP HFT3_BMC_PASSWORD
expect <<'EXP' 2>&1 | tee "$LOG_DIR/sol_expo.log"
set timeout 120
spawn ipmitool -I lanplus -H $env(HFT3_BMC_IP) -U admin -P $env(HFT3_BMC_PASSWORD) sol activate
expect {
    -re "SOL session closed|error|Error" { exit 1 }
    timeout { }
}
# AMI: F7 advanced, search common EXPO paths — send keys with delays
send "\033"
sleep 2
send "F7\r"
sleep 3
# Down to OC/DRAM area — heuristic key spam with pauses (board-specific)
foreach key {Down Down Down Down Down Down Down Down Down Down Return Down Down Return} {
    send "$key"
    sleep 0.8
}
send "F10\r"
sleep 1
send "Y\r"
expect eof
EXP

echo "SOL automation finished — waiting for boot"
sleep 120
bash /root/hft3/repo/infrastructure/chi404/15_post_bios_oc_verify.sh 2>&1 | tee "$LOG_DIR/post_sol_verify.log"
