#!/bin/bash
# Policy A: disable SMT via sysfs when supported.
set -euo pipefail

RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"

NPROC_BEFORE=$(lscpu -b -p=CPU,ONLINE 2>/dev/null | awk -F, '$2==1' | wc -l)
[[ "$NPROC_BEFORE" -lt 1 ]] && NPROC_BEFORE=$(nproc)
echo "nproc before SMT policy: $NPROC_BEFORE" | tee "$LOG_DIR/smt.txt"

if [[ -f /sys/devices/system/cpu/smt/control ]]; then
  echo off > /sys/devices/system/cpu/smt/control || true
  sleep 2
fi

NPROC_AFTER=$(lscpu -b -p=CPU,ONLINE 2>/dev/null | awk -F, '$2==1' | wc -l)
[[ "$NPROC_AFTER" -lt 1 ]] && NPROC_AFTER=$(nproc)
echo "nproc after SMT policy: $NPROC_AFTER" | tee -a "$LOG_DIR/smt.txt"

# Compute HOT_CPUS
OS_CPU="${HFT3_OS_CPU:-0}"
RITHMIC_CPU="${HFT3_RITHMIC_CPU:-1}"
LAST=$((NPROC_AFTER - 1))
if [[ "$LAST" -lt 2 ]]; then
  echo "ERROR: need at least 3 CPUs" >&2
  exit 1
fi
HOT_CPUS="2-${LAST}"

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
touch "$ENV_FILE"
grep -q '^HOT_CPUS=' "$ENV_FILE" && sed -i "s/^HOT_CPUS=.*/HOT_CPUS=${HOT_CPUS}/" "$ENV_FILE" || echo "HOT_CPUS=${HOT_CPUS}" >> "$ENV_FILE"
grep -q '^HFT3_ISOL_CPUS=' "$ENV_FILE" && sed -i "s/^HFT3_ISOL_CPUS=.*/HFT3_ISOL_CPUS=${HOT_CPUS}/" "$ENV_FILE" || echo "HFT3_ISOL_CPUS=${HOT_CPUS}" >> "$ENV_FILE"
grep -q '^HFT3_OS_CPU=' "$ENV_FILE" || echo "HFT3_OS_CPU=${OS_CPU}" >> "$ENV_FILE"
grep -q '^HFT3_RITHMIC_CPU=' "$ENV_FILE" || echo "HFT3_RITHMIC_CPU=${RITHMIC_CPU}" >> "$ENV_FILE"

echo "HOT_CPUS=$HOT_CPUS" | tee -a "$LOG_DIR/smt.txt"

if [[ "$NPROC_BEFORE" != "$NPROC_AFTER" ]]; then
  echo "SMT_CHANGED=1" > "$LOG_DIR/smt_reboot_required"
  echo "SMT change requires reboot."
else
  echo "SMT_CHANGED=0" > "$LOG_DIR/smt_reboot_required"
fi
