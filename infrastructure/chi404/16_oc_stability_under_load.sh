#!/bin/bash
# Full OC stability: post-BIOS verify + jitter + optional broker sweep under market load.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

log() { echo "$*" | tee -a "$LOG_DIR/stability.log"; }

log "=== OC stability under load RUN_ID=$RUN_ID ==="

export RUN_ID HFT3_OC_LOG_DIR="$LOG_DIR"
bash "$SCRIPT_DIR/15_post_bios_oc_verify.sh" 2>&1 | tee -a "$LOG_DIR/stability.log"

export HFT3_TUNING_LOG_DIR="$LOG_DIR/jitter"
bash "$SCRIPT_DIR/05_jitter_gate.sh" 2>&1 | tee -a "$LOG_DIR/stability.log"
if ! grep -q 'JITTER_GATE=PASS' "$LOG_DIR/jitter/jitter_gate_result" 2>/dev/null; then
  log "OC_STABILITY=FAIL (jitter)"
  echo "OC_STABILITY=FAIL" > "$LOG_DIR/result.txt"
  exit 1
fi

if [[ "${HFT3_OC_MARKET_LOAD:-0}" == "1" ]]; then
  log "Market-load phase enabled"
  if [[ "${HFT3_OC_RUN_BROKER_SWEEP:-0}" == "1" ]]; then
    log "Running broker latency sweep (canonical path)"
    cd "$REPO"
    export BROKER_LATENCY_RUN_ID="${BROKER_LATENCY_RUN_ID:-${RUN_ID}_broker}"
    bash scripts/chi404_run_broker_latency_sweep.sh 2>&1 | tee -a "$LOG_DIR/stability.log"
  else
    log "FAIL: HFT3_OC_MARKET_LOAD=1 requires HFT3_OC_RUN_BROKER_SWEEP=1 for full pass"
    echo "OC_STABILITY=FAIL" > "$LOG_DIR/result.txt"
    exit 1
  fi
  log "OC_STABILITY=PASS"
  echo "OC_STABILITY=PASS" > "$LOG_DIR/result.txt"
else
  log "OC_STABILITY=JITTER_PASS (set HFT3_OC_MARKET_LOAD=1 + HFT3_OC_RUN_BROKER_SWEEP=1 during RTH for full pass)"
  echo "OC_STABILITY=JITTER_PASS" > "$LOG_DIR/result.txt"
fi

export HFT3_HW_BASELINE_DIR="$LOG_DIR/post_oc_baseline"
bash "$SCRIPT_DIR/00_hardware_baseline_capture.sh" 2>&1 | tee -a "$LOG_DIR/stability.log"

echo "RUN_ID=$RUN_ID"
