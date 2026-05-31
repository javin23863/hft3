#!/bin/bash
# EXPO via SOL keyboard automation — only after OOB preflight + explicit confirm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

log() { echo "$*" | tee -a "$LOG_DIR/expo_sol.log"; }

log "=== EXPO SOL (preflight gated) RUN_ID=$RUN_ID ==="

bash "$SCRIPT_DIR/17a_oob_preflight.sh" 2>&1 | tee -a "$LOG_DIR/expo_sol.log"

if [[ "${HFT3_OOB_CONFIRMED:-0}" != "1" ]]; then
  log "ABORT: set HFT3_OOB_CONFIRMED=1 after workstation iKVM/IPMI tunnel probe"
  exit 1
fi

apt-get install -y expect >/dev/null 2>&1 || true

export HFT3_BMC_IP="${HFT3_BMC_IP:-10.10.91.93}" HFT3_BMC_PASSWORD

# SOL session in background; reboot to BIOS once SOL is attached
expect <<'EXP' >"$LOG_DIR/sol_expo.log" 2>&1 &
set timeout 180
log_user 1
spawn ipmitool -I lanplus -H $env(HFT3_BMC_IP) -U admin -P $env(HFT3_BMC_PASSWORD) sol activate
expect {
    -re "SOL session closed|error|Error" { exit 1 }
    timeout { }
}
sleep 5
send "\033"
sleep 2
send "F7\r"
sleep 3
foreach key {Down Down Down Down Down Down Down Down Down Down Return Down Down Return} {
    send "$key"
    sleep 0.8
}
send "F10\r"
sleep 1
send "Y\r"
expect eof
EXP
SOL_PID=$!
log "SOL expect pid=$SOL_PID"

sleep 3
ipmitool chassis bootdev bios 2>&1 | tee -a "$LOG_DIR/expo_sol.log"
ipmitool chassis power reset 2>&1 | tee -a "$LOG_DIR/expo_sol.log"

wait "$SOL_PID" || log "WARN: SOL expect exited non-zero (see sol_expo.log)"

log "Waiting for Linux boot..."
sleep 120

bash "$SCRIPT_DIR/24_recover_boot_to_disk.sh" 2>&1 | tee -a "$LOG_DIR/expo_sol.log" || true

sleep 60
bash "$SCRIPT_DIR/15_post_bios_oc_verify.sh" 2>&1 | tee "$LOG_DIR/post_expo_verify.log"
echo "RUN_ID=$RUN_ID"
