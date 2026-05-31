#!/bin/bash
# Pre-BIOS OC: restore point + current CPU/memory snapshot for operator.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

log() { echo "$*" | tee -a "$LOG_DIR/readiness.log"; }

log "=== CHI404 BIOS OC readiness RUN_ID=$RUN_ID ==="

export RESTORE_ID="${RESTORE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
bash "$SCRIPT_DIR/00_restore_point_capture.sh" 2>&1 | tee -a "$LOG_DIR/readiness.log"
echo "$RESTORE_ID" > "$LOG_DIR/RESTORE_ID.txt"

export RUN_ID HFT3_HW_BASELINE_DIR="$LOG_DIR/pre_bios_baseline"
bash "$SCRIPT_DIR/00_hardware_baseline_capture.sh" 2>&1 | tee -a "$LOG_DIR/readiness.log"

{
  echo "board:"
  dmidecode -t baseboard 2>/dev/null | grep -E 'Manufacturer|Product|Version' || true
  echo "memory_speeds:"
  dmidecode -t memory 2>/dev/null | grep -E 'Configured Memory Speed|Speed:' || true
  echo "cpu_mhz_sample:"
  for c in 0 2 5 11; do
    f="/sys/devices/system/cpu/cpu${c}/cpufreq/scaling_cur_freq"
    [[ -f "$f" ]] && echo "cpu${c}=$(cat "$f")" || true
  done
} | tee "$LOG_DIR/pre_oc_snapshot.txt"

log "RESTORE_ID=$RESTORE_ID"
log "Next: UEFI per docs/chi404/CPU_MEMORY_OVERCLOCK.md then 15_post_bios_oc_verify.sh"
log "Logs: $LOG_DIR"
echo "RUN_ID=$RUN_ID"
echo "RESTORE_ID=$RESTORE_ID"
