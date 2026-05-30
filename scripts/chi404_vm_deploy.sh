#!/usr/bin/env bash
# Deploy headless R|Trader sidecar on CHI404 (WinRM + autostart + health check).
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"

[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

if [[ -z "${VM_ADMIN_PASSWORD:-}" ]]; then
  echo "Set VM_ADMIN_PASSWORD in $ENV_FILE (must match Windows Administrator password)" >&2
  exit 1
fi

export VM_WINRM_HOST="${VM_WINRM_HOST:-$(
  virsh domifaddr hft3-rtrader-win 2>/dev/null | awk '/ipv4/ {print $4}' | head -1 | cut -d/ -f1
)}"
if [[ -z "$VM_WINRM_HOST" ]]; then
  echo "Could not resolve VM IP; set VM_WINRM_HOST in $ENV_FILE" >&2
  exit 1
fi

echo "=== Deploy headless to $VM_WINRM_HOST ==="
python3 "$REPO/scripts/chi404_vm_apply_headless.py"
python3 "$REPO/scripts/chi404_vm_remap_smb.py"

virsh autostart hft3-rtrader-win 2>/dev/null || true

# Remove debug probe files from watch lane
rm -f /root/hft3/rtrader_watch/*_probe.txt

echo "=== Restart capture ==="
bash "$REPO/scripts/chi404_setup_vm_bridge.sh"

echo "=== VM health ==="
python3 "$REPO/scripts/chi404_vm_status_check.py"

echo "=== Watch dir ==="
find /root/hft3/rtrader_watch -maxdepth 1 \( -name '*.log' -o -name '*.cur.txt' \) -printf '%f %s\n' 2>/dev/null || true
