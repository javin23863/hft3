#!/bin/bash
# PDF §3.2 runtime idle disable — idempotent; safe to run after every reboot.
set -euo pipefail

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_MEMORY_LOG_DIR:-/root/hft3/logs/memory_upgrade/${RUN_ID}}"
mkdir -p "$LOG_DIR"
OUT="$LOG_DIR/memory_idle_apply.txt"

log() { echo "$*" | tee -a "$OUT"; }

log "=== memory idle apply RUN_ID=$RUN_ID ==="

if ! command -v cpupower >/dev/null; then
  log "ERROR cpupower not installed — required for PDF §3 idle-set"
  exit 1
fi

log "[cpupower idle before]"
cpupower idle-info 2>&1 | tee -a "$OUT" || true

if ! cpupower idle-set -D 0 2>&1 | tee -a "$OUT"; then
  log "ERROR cpupower idle-set -D 0 failed"
  exit 1
fi
log "cpupower idle-set -D 0 OK"

log "[cpupower idle after]"
cpupower idle-info 2>&1 | tee -a "$OUT" || true
cpupower frequency-set -g performance 2>&1 | tee -a "$OUT" || true

echo "MEMORY_IDLE_APPLY=done" > "$LOG_DIR/memory_idle_apply_result"
log "Idle apply complete. logs=$LOG_DIR"
