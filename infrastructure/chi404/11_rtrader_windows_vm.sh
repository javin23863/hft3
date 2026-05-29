#!/bin/bash
# Create KVM Windows VM for native R|Trader on CHI404.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
INSTALLERS="/root/hft3/installers"
VM_DIR="/var/lib/libvirt/images/hft3-rtrader"
LOG_DIR="/root/hft3/logs/rtrader"
VM_NAME="${RTRADER_VM_NAME:-hft3-rtrader-win}"
WIN_ISO="${WINDOWS_ISO:-${INSTALLERS}/windows.iso}"
VIRTIO_ISO="${INSTALLERS}/virtio-win.iso"
DISK="${VM_DIR}/${VM_NAME}.qcow2"
AUTOUNATTEND_ISO="${INSTALLERS}/autounattend.iso"
AUTOUNATTEND_FLOPPY="${INSTALLERS}/autounattend.img"
WATCH="/root/hft3/rtrader_watch"

mkdir -p "$INSTALLERS" "$VM_DIR" "$LOG_DIR" "$WATCH"
# libvirt-qemu (uid 64055) must traverse parents and read ISOs
chmod o+x /root /root/hft3 "$INSTALLERS" 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq qemu-kvm libvirt-daemon-system libvirt-clients virtinst genisoimage wget 2>&1 | tee -a "$LOG_DIR/vm_setup.log"

systemctl enable --now libvirtd
virsh net-start default 2>/dev/null || true
virsh net-autostart default 2>/dev/null || true

if [[ ! -f "$VIRTIO_ISO" ]]; then
  echo "Downloading virtio-win.iso..." | tee -a "$LOG_DIR/vm_setup.log"
  wget -q -O "$VIRTIO_ISO" "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
fi

if [[ ! -f "$WIN_ISO" ]]; then
  echo "ERROR: Windows ISO not found at $WIN_ISO" | tee -a "$LOG_DIR/vm_setup.log"
  echo "Upload from workstation: scp Win11.iso chi404:${WIN_ISO}" | tee -a "$LOG_DIR/vm_setup.log"
  exit 1
fi

# Floppy with autounattend + guest setup (Windows scans A:\autounattend.xml)
if [[ -f "$REPO/infrastructure/chi404/autounattend.xml" ]]; then
  STAGE="/tmp/hft3-autounattend"
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  cp -f "$REPO/infrastructure/chi404/autounattend.xml" "$STAGE/"
  cp -f "$REPO/scripts/chi404_vm_guest_setup.ps1" "$STAGE/" 2>/dev/null || true
  cp -f "$REPO/scripts/chi404_vm_rtrader_login.ps1" "$STAGE/" 2>/dev/null || true
  [[ -f "${WATCH}/rtrader_smb.env" ]] && cp -f "${WATCH}/rtrader_smb.env" "$STAGE/"
  [[ -f "${WATCH}/rithmic_login.env" ]] && cp -f "${WATCH}/rithmic_login.env" "$STAGE/"
  mkfs.vfat -C "$AUTOUNATTEND_FLOPPY" 1440 2>/dev/null || true
  MTOOLS_SKIP_CHECK=1 mcopy -i "$AUTOUNATTEND_FLOPPY" -s "$STAGE"/* :: 2>/dev/null || \
  genisoimage -o "$AUTOUNATTEND_ISO" -input-charset utf8 "$STAGE"/*
fi

# Stage guest setup + credentials + R|Trader zip on SMB share
cp -f "$REPO/scripts/chi404_vm_guest_setup.ps1" "$WATCH/" 2>/dev/null || true
cp -f "$INSTALLERS/rithmic_portable.zip" "$WATCH/" 2>/dev/null || true
[[ -f "${WATCH}/rtrader_smb.env" ]] && cp -f "${WATCH}/rtrader_smb.env" "$INSTALLERS/rtrader_smb.env"

if virsh dominfo "$VM_NAME" &>/dev/null; then
  echo "VM $VM_NAME already exists" | tee -a "$LOG_DIR/vm_setup.log"
  virsh start "$VM_NAME" 2>/dev/null || true
else
  echo "Creating VM $VM_NAME..." | tee -a "$LOG_DIR/vm_setup.log"
  virt-install \
    --name "$VM_NAME" \
    --memory 8192 \
    --vcpus 4 \
    --cpu host-passthrough \
    --disk path="$DISK",size=60,format=qcow2,bus=sata \
    --cdrom "$WIN_ISO" \
    --disk path="$VIRTIO_ISO",device=cdrom \
    --os-variant win2k22 \
    --network network=default,model=e1000 \
    --graphics vnc,listen=127.0.0.1 \
    --noautoconsole \
    --boot cdrom,hd
fi

# Never use --remove-all-storage on undefine — it deletes attached ISO paths under INSTALLERS.

virsh autostart "$VM_NAME" 2>/dev/null || true
echo "VM state: $(virsh domstate "$VM_NAME" 2>/dev/null || echo unknown)"
echo "VNC: ssh -L 5900:127.0.0.1:5900 chi404 then connect to localhost:5900 if install needs console"
