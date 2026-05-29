#!/bin/bash
# CHI404 tuning orchestrator. Re-run same RUN_ID after reboot: HFT3_TUNING_RESUME_STEP=N
set -euo pipefail

export RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export HFT3_TUNING_LOG_DIR="/root/hft3/logs/tuning/${RUN_ID}"
export HFT3_ENV_FILE="/root/hft3/.env"
mkdir -p "$HFT3_TUNING_LOG_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INFRA="$(cd "$SCRIPT_DIR/.." && pwd)"
RESUME="${HFT3_TUNING_RESUME_STEP:-0}"

manifest() {
  echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"step\":\"$1\",\"status\":\"$2\"}" >> "$HFT3_TUNING_LOG_DIR/manifest.jsonl"
}

step() { echo "===== $1 ====="; manifest "$1" "start"; }

if [[ "$RESUME" -le 0 ]]; then
  step "00_baseline"
  bash "$SCRIPT_DIR/00_baseline_capture.sh"
  step "00_install_tools"
  bash "$SCRIPT_DIR/00_install_tools.sh"
  step "02_smt"
  bash "$SCRIPT_DIR/02_smt_policy.sh"
  if [[ -f "$HFT3_TUNING_LOG_DIR/smt_reboot_required" ]] && grep -q 'SMT_CHANGED=1' "$HFT3_TUNING_LOG_DIR/smt_reboot_required"; then
    manifest "reboot_smt" "required"
    echo "Reboot for SMT then: HFT3_TUNING_RESUME_STEP=2 RUN_ID=$RUN_ID bash $0"
    sleep 2
    reboot
  fi
fi

if [[ "$RESUME" -le 2 ]]; then
  step "03_chrony"
  bash "$SCRIPT_DIR/03_chrony_gate.sh"
fi

if [[ "$RESUME" -le 3 ]]; then
  step "01_kernel"
  echo "WARNING: GRUB change — provider console if SSH fails after reboot."
  bash "$INFRA/01_kernel_tuning.sh"
  manifest "reboot_kernel" "required"
  echo "Reboot for kernel then: HFT3_TUNING_RESUME_STEP=4 RUN_ID=$RUN_ID bash $0"
  sleep 2
  reboot
fi

if [[ "$RESUME" -le 4 ]]; then
  step "04_irq_net"
  bash "$SCRIPT_DIR/04_irq_net_tuning.sh"
  step "06_cpuset"
  bash "$SCRIPT_DIR/06_cpuset_systemd.sh"
  step "05_jitter"
  bash "$SCRIPT_DIR/05_jitter_gate.sh"
  step "07_chrony_colo"
  bash "$SCRIPT_DIR/07_chrony_colo_notes.sh"
  step "03_latency_report"
  export REPORT_DIR="$HFT3_TUNING_LOG_DIR"
  bash "$INFRA/03_latency_report.sh"
  step "validate"
  python3 "$SCRIPT_DIR/validate_pass_criteria.py" "$SCRIPT_DIR/PASS_CRITERIA.json" "$HFT3_TUNING_LOG_DIR"
  manifest "complete" "done"
  cat "$HFT3_TUNING_LOG_DIR/PASS_FAIL.txt"
  echo "RUN_ID=$RUN_ID logs=$HFT3_TUNING_LOG_DIR"
fi
