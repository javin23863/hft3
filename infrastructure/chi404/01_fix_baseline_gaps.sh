#!/bin/bash
# Fix documented CHI404 baseline gaps: .env CPU/NIC sync + IRQ/net tuning + idle re-apply.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_FIX_LOG_DIR:-/root/hft3/logs/baseline_fix/${RUN_ID}}"
mkdir -p "$LOG_DIR"

log() { echo "$*" | tee -a "$LOG_DIR/fix.log"; }

log "=== CHI404 baseline gap fix RUN_ID=$RUN_ID ==="

# nproc may reflect SSH cpuset; use lscpu online list
_online=$(lscpu 2>/dev/null | awk -F: '/On-line CPU\(s\) list/{gsub(/ /,"",$2); print $2}')
if [[ "$_online" == *-* ]]; then
  _first=${_online%-*}
  _last=${_online#*-}
  NPROC=$((_last - _first + 1))
  LAST=$_last
elif [[ "$_online" == *,* ]]; then
  LAST=$(echo "$_online" | tr ',' '\n' | sort -n | tail -1)
  NPROC=$((LAST + 1))
else
  NPROC=$(nproc)
  LAST=$((NPROC - 1))
fi
if [[ "$LAST" -lt 2 ]]; then
  echo "ERROR: need at least 3 CPUs" >&2
  exit 1
fi
HOT_CPUS="2-${LAST}"
OS_CPU="${HFT3_OS_CPU:-0}"
RITHMIC_CPU="${HFT3_RITHMIC_CPU:-1}"
NIC="${HFT3_NIC:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"
GW=$(ip route | awk '/default/ {print $3; exit}')

touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
set_kv() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

log "Sync .env: HOT_CPUS=$HOT_CPUS OS=$OS_CPU RITHMIC=$RITHMIC_CPU NIC=$NIC"
set_kv "HOT_CPUS" "$HOT_CPUS"
set_kv "HFT3_ISOL_CPUS" "$HOT_CPUS"
set_kv "HFT3_OS_CPU" "$OS_CPU"
set_kv "HFT3_RITHMIC_CPU" "$RITHMIC_CPU"
set_kv "HFT3_NIC" "$NIC"
[[ -n "$GW" ]] && set_kv "HFT3_GATEWAY_IP" "$GW"

export HOT_CPUS HFT3_ISOL_CPUS="$HOT_CPUS" HFT3_OS_CPU="$OS_CPU" HFT3_RITHMIC_CPU="$RITHMIC_CPU"
export HFT3_NIC="$NIC" HFT3_ENV_FILE="$ENV_FILE"
export RUN_ID HFT3_TUNING_LOG_DIR="$LOG_DIR/tuning"

log "Run IRQ/net tuning (04_irq_net_tuning.sh)"
bash "$SCRIPT_DIR/04_irq_net_tuning.sh" 2>&1 | tee -a "$LOG_DIR/fix.log"

log "Re-apply cpupower idle-set + performance governor"
export HFT3_MEMORY_LOG_DIR="$LOG_DIR/memory_idle"
bash "$SCRIPT_DIR/12_memory_idle_apply.sh" 2>&1 | tee -a "$LOG_DIR/fix.log"

log "Enable net tuning on boot (13_net_tune_onboot.sh)"
bash "$SCRIPT_DIR/13_net_tune_onboot.sh" 2>&1 | tee -a "$LOG_DIR/fix.log"

log "Capture hardware baseline JSON"
export HFT3_HW_BASELINE_DIR="$LOG_DIR/hardware_baseline"
export HFT3_CAPTURE_METHOD="01_fix_baseline_gaps.sh + 00_hardware_baseline_capture.sh"
bash "$SCRIPT_DIR/00_hardware_baseline_capture.sh" 2>&1 | tee -a "$LOG_DIR/fix.log"

log "Validate NIC offloads post-fix"
ethtool -k "$NIC" 2>&1 | tee "$LOG_DIR/ethtool_k.txt" | tee -a "$LOG_DIR/fix.log"

FAIL=0
for off in generic-receive-offload generic-segmentation-offload tcp-segmentation-offload large-receive-offload; do
  if grep -qi "${off}: on" "$LOG_DIR/ethtool_k.txt"; then
    log "WARN offload still on: $off"
    FAIL=1
  fi
done

if [[ "$FAIL" -eq 0 ]]; then
  log "BASELINE_FIX=PASS"
  echo "BASELINE_FIX=PASS" > "$LOG_DIR/result.txt"
else
  log "BASELINE_FIX=PARTIAL (check ethtool_k.txt — driver may not allow offload disable)"
  echo "BASELINE_FIX=PARTIAL" > "$LOG_DIR/result.txt"
  log "Logs: $LOG_DIR"
  echo "RUN_ID=$RUN_ID"
  exit 1
fi

log "Logs: $LOG_DIR"
log "Baseline JSON: $LOG_DIR/hardware_baseline/baseline.json"
echo "RUN_ID=$RUN_ID"
