#!/bin/bash
# Disable Windows password complexity offline, skip OOBE, blank Administrator password.
set -euo pipefail
VM="${1:-hft3-rtrader-win}"
DISK="/var/lib/libvirt/images/hft3-rtrader/${VM}.qcow2"
MNT=/mnt/hft3win
REG=/root/hft3/repo/scripts/disable_pw_complex.reg
OOBE=/root/hft3/repo/scripts/skip_oobe.reg
SETUP=/root/hft3/repo/scripts/finish_setup.reg
SAM="$MNT/Windows/System32/config/SAM"
SYSTEM="$MNT/Windows/System32/config/SYSTEM"
SOFTWARE="$MNT/Windows/System32/config/SOFTWARE"

virsh destroy "$VM" 2>/dev/null || true
guestunmount "$MNT" 2>/dev/null || true
find "$MNT" -mindepth 1 -delete 2>/dev/null || true
mkdir -p "$MNT"

# Windows leaves NTFS dirty; ntfsfix + remove_hiberfile enables rw mount on sda2.
guestfish --rw -a "$DISK" run : ntfsfix /dev/sda2
guestmount -a "$DISK" -m /dev/sda2:/:remove_hiberfile --rw "$MNT"

reged -C -I "$SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$REG"
reged -C -I "$SYSTEM" 'HKEY_LOCAL_MACHINE\SYSTEM' "$SETUP"
reged -C -I "$SOFTWARE" 'HKEY_LOCAL_MACHINE\SOFTWARE' "$OOBE"
printf '1\ny\nq\n' | chntpw -u Administrator "$SAM"

guestunmount "$MNT"
virsh start "$VM"
echo "Offline OOBE skip + blank Administrator password applied."
