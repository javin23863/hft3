#!/bin/bash
# Drive Windows install + OOBE password on CHI404 (VNC).
set -euo pipefail
VNC="${VNC:-127.0.0.1::5900}"
DISK="/var/lib/libvirt/images/hft3-rtrader/hft3-rtrader-win.qcow2"
PW="${VM_ADMIN_PASSWORD:-Password1!}"

bash /root/hft3/repo/scripts/chi404_vm_vnc_install.sh

# Custom install double-click + disk Next (if not already installing)
vncdo -s "$VNC" move 512 430 click 1; sleep 1
vncdo -s "$VNC" move 512 430 click 1; sleep 3
vncdo -s "$VNC" key alt-n; sleep 15

echo "Waiting for install + OOBE (disk > 8G)..."
for i in $(seq 1 40); do
  sz=$(du -m "$DISK" | cut -f1)
  echo "disk=${sz}M"
  [[ "$sz" -gt 8000 ]] && break
  sleep 30
done

echo "Setting OOBE password via VNC..."
vncdo -s "$VNC" move 420 340 click 1; sleep 0.5
vncdo -s "$VNC" type "$PW"; sleep 0.5
vncdo -s "$VNC" key tab; sleep 0.5
vncdo -s "$VNC" type "$PW"; sleep 0.5
vncdo -s "$VNC" move 920 715 click 1; sleep 120

vncdo -s "$VNC" capture /tmp/vnc-post-oobe.png
virsh domifaddr hft3-rtrader-win 2>/dev/null || true
echo "Done. Admin password: $PW"
