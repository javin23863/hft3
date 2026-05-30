#!/bin/bash
# CHI404 memory upgrade: restore point → PDF gap-fill → validate.
# Does NOT re-run full run_chi404_tuning.sh pipeline.
set -euo pipefail

export RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export HFT3_MEMORY_LOG_DIR="/root/hft3/logs/memory_upgrade/${RUN_ID}"
export HFT3_TUNING_LOG_DIR="$HFT3_MEMORY_LOG_DIR"
export HFT3_ENV_FILE="/root/hft3/.env"
export HFT3_VALIDATE_PROFILE="${HFT3_VALIDATE_PROFILE:-memory_upgrade}"
mkdir -p "$HFT3_MEMORY_LOG_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESUME="${HFT3_MEMORY_RESUME_STEP:-0}"
RESTORE_ID="${RESTORE_ID:-}"

case "$RESUME" in
  0|4) ;;
  *)
    echo "ERROR: HFT3_MEMORY_RESUME_STEP must be 0 (start) or 4 (post-reboot resume), got $RESUME" >&2
    exit 1
    ;;
esac

load_restore_id() {
  if [[ -n "$RESTORE_ID" ]]; then
    return 0
  fi
  if [[ -f "$HFT3_MEMORY_LOG_DIR/RESTORE_ID.txt" ]]; then
    RESTORE_ID=$(grep RESTORE_ID= "$HFT3_MEMORY_LOG_DIR/RESTORE_ID.txt" | cut -d= -f2-)
    export RESTORE_ID
  fi
}

grub_stale_reboot_tokens() {
  local token
  GRUB_FILE="/etc/default/grub"
  local -a tokens=(rcu_nocb_poll idle=poll acpi_irq_nobalance)
  if lscpu 2>/dev/null | grep -qiE 'vendor id.*intel'; then
    tokens+=(intel_idle.max_cstate=0)
  fi
  for token in "${tokens[@]}"; do
    if [[ "${HFT3_SKIP_IDLE_POLL:-0}" == "1" && "$token" == "idle=poll" ]]; then
      continue
    fi
    if grep -qF "$token" "$GRUB_FILE" 2>/dev/null && ! grep -qF "$token" /proc/cmdline; then
      echo "$token"
    fi
  done
}

manifest() {
  echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"step\":\"$1\",\"status\":\"$2\"}" >> "$HFT3_MEMORY_LOG_DIR/manifest.jsonl"
}

step() { echo "===== $1 ====="; manifest "$1" "start"; }

if [[ "$RESUME" -eq 0 ]]; then
  if [[ -f "$HFT3_MEMORY_LOG_DIR/MEMORY_UPGRADE_RESULT.txt" ]]; then
    echo "ERROR: log dir already has MEMORY_UPGRADE_RESULT.txt — use HFT3_MEMORY_RESUME_STEP=4 RUN_ID=$RUN_ID" >&2
    exit 1
  fi
  step "00_restore_point_capture"
  export RESTORE_ID="${RESTORE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
  bash "$SCRIPT_DIR/00_restore_point_capture.sh" | tee "$HFT3_MEMORY_LOG_DIR/restore_capture.log"
  echo "RESTORE_ID=${RESTORE_ID}" > "$HFT3_MEMORY_LOG_DIR/RESTORE_ID.txt"
  manifest "00_restore_point_capture" "done"

  step "12_memory_gap_fill"
  bash "$SCRIPT_DIR/12_memory_gap_fill.sh" | tee "$HFT3_MEMORY_LOG_DIR/gap_fill.log"
  manifest "12_memory_gap_fill" "done"

  NEED_REBOOT=0
  GRUB_FLAG="$HFT3_MEMORY_LOG_DIR/memory_grub_changed"
  if [[ -f "$GRUB_FLAG" ]] && grep -q 'MEMORY_GRUB_CHANGED=1' "$GRUB_FLAG"; then
    NEED_REBOOT=1
  fi
  stale=$(grub_stale_reboot_tokens || true)
  if [[ -n "$stale" ]]; then
    NEED_REBOOT=1
    echo "GRUB/cmdline stale for: $stale"
  fi
  if [[ "$NEED_REBOOT" -eq 1 ]]; then
    manifest "reboot_memory_grub" "required"
    echo "GRUB changed or cmdline stale — reboot required."
    echo "Resume: HFT3_MEMORY_RESUME_STEP=4 RUN_ID=$RUN_ID RESTORE_ID=$RESTORE_ID bash $0"
    sleep 2
    reboot
  fi
fi

if [[ "$RESUME" -eq 4 || "$RESUME" -eq 0 ]]; then
  load_restore_id
  if [[ -z "$RESTORE_ID" ]]; then
    echo "ERROR: RESTORE_ID required (set env or ensure RESTORE_ID.txt in log dir)" >&2
    exit 1
  fi

  step "12_memory_idle_apply"
  bash "$SCRIPT_DIR/12_memory_idle_apply.sh" | tee "$HFT3_MEMORY_LOG_DIR/idle_apply.log"
  manifest "12_memory_idle_apply" "done"

  step "05_jitter_gate"
  bash "$SCRIPT_DIR/05_jitter_gate.sh" | tee "$HFT3_MEMORY_LOG_DIR/jitter_gate.log"
  manifest "05_jitter_gate" "done"

  step "validate"
  python3 "$SCRIPT_DIR/validate_pass_criteria.py" "$SCRIPT_DIR/PASS_CRITERIA.json" "$HFT3_MEMORY_LOG_DIR"
  VALID_EC=$?
  manifest "validate" "done"

  RESULT="$HFT3_MEMORY_LOG_DIR/MEMORY_UPGRADE_RESULT.txt"
  if [[ "$VALID_EC" -eq 0 ]]; then
    echo "PASS RESTORE_ID=${RESTORE_ID} RUN_ID=${RUN_ID}" | tee "$RESULT"
    manifest "complete" "pass"
  else
    echo "FAIL RESTORE_ID=${RESTORE_ID} RUN_ID=${RUN_ID}" | tee "$RESULT"
    echo "Rollback: RESTORE_ID=${RESTORE_ID} bash $SCRIPT_DIR/00_restore_point_restore.sh --reboot"
    manifest "complete" "fail"
    exit 1
  fi

  echo "RUN_ID=$RUN_ID RESTORE_ID=$RESTORE_ID logs=$HFT3_MEMORY_LOG_DIR"
fi
