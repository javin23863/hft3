#!/bin/bash
# CHI404 baseline capture (no chrony required).
set -euo pipefail

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export HFT3_TUNING_LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
LOG_DIR="$HFT3_TUNING_LOG_DIR"
mkdir -p "$LOG_DIR"

OUT="$LOG_DIR/baseline.txt"
{
  echo "=== CHI404 baseline RUN_ID=$RUN_ID ==="
  date -u
  echo ""
  echo "[hostname]"
  hostname
  echo "[virt]"
  systemd-detect-virt || true
  echo "[nproc]"
  nproc
  echo "[lscpu]"
  lscpu
  echo "[cmdline]"
  cat /proc/cmdline
  echo ""
  echo "[smt]"
  if [[ -f /sys/devices/system/cpu/smt/active ]]; then
    cat /sys/devices/system/cpu/smt/active
    cat /sys/devices/system/cpu/smt/control 2>/dev/null || true
  else
    echo "smt sysfs missing"
  fi
  echo ""
  echo "[load]"
  uptime
  if command -v mpstat >/dev/null; then
    mpstat -P ALL 1 3 || true
  else
    echo "mpstat not installed"
  fi
  echo ""
  echo "[network interfaces]"
  ip -br link || true
  ip route get 1.1.1.1 2>/dev/null || true
  echo ""
  echo "[memory]"
  free -h
} | tee "$OUT"

echo "Baseline written to $OUT"
