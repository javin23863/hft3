#!/bin/bash
# Roll back CHI404 to a prior restore point (never runs gap-fill).
set -euo pipefail

RESTORE_ID="${RESTORE_ID:-}"
RESTORE_ROOT="${HFT3_RESTORE_ROOT:-/root/hft3/restore_points}"
DO_REBOOT=0

for arg in "$@"; do
  case "$arg" in
    --reboot) DO_REBOOT=1 ;;
    -h|--help)
      echo "Usage: RESTORE_ID=<id> bash $0 [--reboot]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$RESTORE_ID" ]]; then
  echo "ERROR: set RESTORE_ID" >&2
  exit 1
fi

SRC="${RESTORE_ROOT}/${RESTORE_ID}"
if [[ ! -f "$SRC/manifest.json" ]]; then
  echo "ERROR: restore point not found: $SRC/manifest.json" >&2
  exit 1
fi

GRUB_CHANGED=0
CMDLINE_CHANGED=0

if [[ -f "$SRC/etc/default/grub" ]]; then
  if ! cmp -s "$SRC/etc/default/grub" /etc/default/grub 2>/dev/null; then
    cp -a "$SRC/etc/default/grub" /etc/default/grub
    update-grub
    GRUB_CHANGED=1
    CMDLINE_CHANGED=1
  fi
fi

if [[ -f "$SRC/etc/sysctl.d/99-hft3.conf" ]]; then
  cp -a "$SRC/etc/sysctl.d/99-hft3.conf" /etc/sysctl.d/99-hft3.conf
  sysctl -p /etc/sysctl.d/99-hft3.conf || true
fi

if [[ -f "$SRC/root/hft3/.env" ]]; then
  mkdir -p /root/hft3
  cp -a "$SRC/root/hft3/.env" /root/hft3/.env
fi

if compgen -G "$SRC/etc/systemd/system/hft3-*.service" >/dev/null; then
  cp -a "$SRC/etc/systemd/system/hft3-"*.service /etc/systemd/system/ 2>/dev/null || true
fi

for dropin in "$SRC"/etc/systemd/system/hft3-*.service.d; do
  [[ -d "$dropin" ]] || continue
  base=$(basename "$dropin")
  mkdir -p "/etc/systemd/system/$base"
  cp -a "$dropin/." "/etc/systemd/system/$base/"
done

systemctl daemon-reload 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IDLE_WAS_DISABLED=0
if [[ -f "$SRC/manifest.json" ]]; then
  IDLE_WAS_DISABLED=$(python3 -c "import json; m=json.load(open('$SRC/manifest.json')); print(1 if m.get('idle_disabled_at_capture')=='true' else 0)")
fi

echo "RESTORE_ID=${RESTORE_ID} restored from ${SRC}"
if [[ "$IDLE_WAS_DISABLED" -eq 1 ]] && command -v cpupower >/dev/null; then
  echo "Re-applying cpupower idle-set from pre-upgrade snapshot policy..."
  export HFT3_MEMORY_LOG_DIR="${SRC}/restore_idle_apply"
  mkdir -p "$HFT3_MEMORY_LOG_DIR"
  bash "$SCRIPT_DIR/12_memory_idle_apply.sh"
fi
if [[ "$GRUB_CHANGED" -eq 1 ]]; then
  echo "GRUB restored — reboot required for cmdline rollback."
fi

if [[ "$DO_REBOOT" -eq 1 ]]; then
  echo "Rebooting in 5s..."
  sleep 5
  reboot
elif [[ "$CMDLINE_CHANGED" -eq 1 ]]; then
  echo "Run with --reboot to apply cmdline rollback."
  exit 2
fi
