#!/bin/bash
# Document colo NTP recommendation when HFT3_COLO_NTP is unset.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"
OUT="$LOG_DIR/chrony_colo_notes.txt"

{
  echo "=== Chrony colo notes ==="
  echo "Timestamp: $(date -u)"
  if [[ -n "${HFT3_COLO_NTP:-}" ]]; then
    echo "HFT3_COLO_NTP=${HFT3_COLO_NTP} (configured)"
  else
    echo "HFT3_COLO_NTP not set"
    echo "NOTE: RMS offset to public NTP may exceed 500us until colo stratum-1 is configured."
    echo "Recommended: set server <colo-ntp-host> iburst prefer in /etc/chrony/chrony.conf"
  fi
  echo ""
  if command -v chronyc &>/dev/null; then
    chronyc tracking
    echo ""
    chronyc sources -v 2>&1 | head -20
  fi
} | tee "$OUT"

echo "Chrony colo notes written to $OUT"
