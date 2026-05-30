#!/usr/bin/env bash
# Finish CHI404 Rithmic trial: Windows VM + SMB + capture (colo-only).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a
cd "$REPO"

echo "=== Quiesce Wine ==="
systemctl stop hft3-rithmic-trial 2>/dev/null || true
killall -9 wine wineserver winetricks 2>/dev/null || true
sleep 2

echo "=== SMB share ==="
bash "$REPO/infrastructure/chi404/10_rtrader_smb_share.sh"

echo "=== Windows VM (requires ${WINDOWS_ISO:-/root/hft3/installers/windows.iso}) ==="
if [[ -f "${WINDOWS_ISO:-/root/hft3/installers/windows.iso}" ]]; then
  if [[ "${RTRADER_VM_RECREATE:-0}" == "1" ]]; then
    echo "RTRADER_VM_RECREATE=1 — VM will be rebuilt with VirtIO defaults"
  fi
  bash "$REPO/infrastructure/chi404/11_rtrader_windows_vm.sh"
  if grep -q 'using libvirt default NAT' "$REPO/../logs/rtrader/vm_setup.log" 2>/dev/null || \
     grep -q 'using libvirt default NAT' /root/hft3/logs/rtrader/vm_setup.log 2>/dev/null; then
    echo "VM network: NAT fallback (bridge not present)"
  elif virsh dumpxml hft3-rtrader-win 2>/dev/null | grep -q "bridge="; then
    echo "VM network: bridged"
  fi
else
  echo "SKIP VM create — upload windows.iso then re-run 11_rtrader_windows_vm.sh"
fi

echo "=== Linux capture bridge ==="
bash "$REPO/scripts/chi404_setup_vm_bridge.sh"

if virsh domstate hft3-rtrader-win 2>/dev/null | grep -q running; then
  if [[ -n "${VM_ADMIN_PASSWORD:-}" ]]; then
    echo "=== Headless sidecar deploy ==="
    bash "$REPO/scripts/chi404_vm_deploy.sh"
  else
    echo "SKIP headless deploy — set VM_ADMIN_PASSWORD in /root/hft3/.env"
  fi
fi

echo "=== Status ==="
systemctl is-active hft3-rithmic-trial
virsh domstate hft3-rtrader-win 2>/dev/null || echo "vm-not-created"
ls -la /root/hft3/rtrader_watch/ | head -10
