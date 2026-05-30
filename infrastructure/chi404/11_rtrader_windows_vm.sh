#!/bin/bash
# Create KVM Windows VM for native R|Trader on CHI404 (VirtIO defaults; see hft3_vm_modifications.pdf).
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

# Parameterized defaults (override in /root/hft3/.env)
VM_MEMORY="${RTRADER_VM_MEMORY:-16384}"
VM_VCPUS="${RTRADER_VM_VCPUS:-4}"
VM_DISK_SIZE="${RTRADER_VM_DISK_SIZE:-120}"
VM_DISK_BUS="${RTRADER_VM_DISK_BUS:-virtio}"
VM_NIC_MODEL="${RTRADER_VM_NIC_MODEL:-virtio}"
VM_BRIDGE_NAME="${RTRADER_VM_BRIDGE_NAME:-br0}"
VM_RECREATE="${RTRADER_VM_RECREATE:-0}"

preflight_virt() {
  echo "=== Virtualization preflight ==="
  local flags
  flags="$(egrep -c '(vmx|svm)' /proc/cpuinfo || true)"
  echo "CPU virt flags: ${flags}"
  if [[ ! -e /dev/kvm ]]; then
    cat <<'EOF'
ERROR: /dev/kvm missing. Ask the provider to enable AMD-V/Intel VT-x in BIOS/IPMI
and confirm /dev/kvm is available inside Linux before creating the VM.
EOF
    exit 1
  fi
  ls -l /dev/kvm
  lsmod | grep kvm || true
  if ! systemctl is-active --quiet libvirtd; then
    echo "Starting libvirtd..."
    systemctl enable --now libvirtd
  fi
}

select_network_args() {
  if ip link show "$VM_BRIDGE_NAME" &>/dev/null; then
    echo "Network: bridge=${VM_BRIDGE_NAME} model=${VM_NIC_MODEL}" | tee -a "$LOG_DIR/vm_setup.log"
    NETWORK_MODE="bridge"
    NETWORK_ARGS=(--network "bridge=${VM_BRIDGE_NAME},model=${VM_NIC_MODEL}")
  else
    echo "WARNING: bridge ${VM_BRIDGE_NAME} not found; using libvirt default NAT" | tee -a "$LOG_DIR/vm_setup.log"
    virsh net-start default 2>/dev/null || true
    virsh net-autostart default 2>/dev/null || true
    NETWORK_MODE="nat"
    NIC_EFFECTIVE="${RTRADER_VM_NIC_MODEL:-virtio}"
    if [[ "$NIC_EFFECTIVE" == "virtio" ]]; then
      NIC_EFFECTIVE="e1000"
      echo "NAT fallback: using e1000 NIC until guest VirtIO NetKVM is installed" | tee -a "$LOG_DIR/vm_setup.log"
    fi
    NETWORK_ARGS=(--network "network=default,model=${NIC_EFFECTIVE}")
  fi
}

safe_recreate_vm() {
  if ! virsh dominfo "$VM_NAME" &>/dev/null; then
    return 0
  fi
  echo "Recreating VM ${VM_NAME} (RTRADER_VM_RECREATE=1)..." | tee -a "$LOG_DIR/vm_setup.log"
  virsh destroy "$VM_NAME" 2>/dev/null || true
  # Never use --remove-all-storage — it deletes ISO paths under INSTALLERS.
  virsh undefine "$VM_NAME" 2>/dev/null || true
  rm -f "$DISK"
}

ensure_on_reboot_restart() {
  if ! virsh dominfo "$VM_NAME" &>/dev/null; then
    return 0
  fi
  local current
  current="$(virsh dumpxml "$VM_NAME" | sed -n 's:.*<on_reboot>\(.*\)</on_reboot>.*:\1:p' | head -1)"
  if [[ "$current" != "restart" ]]; then
    echo "Setting on_reboot=restart (was: ${current:-unknown})" | tee -a "$LOG_DIR/vm_setup.log"
    virsh dumpxml "$VM_NAME" | sed 's|<on_reboot>destroy</on_reboot>|<on_reboot>restart</on_reboot>|' > /tmp/"${VM_NAME}".xml
    virsh define /tmp/"${VM_NAME}".xml
    rm -f /tmp/"${VM_NAME}".xml
  fi
}

print_existing_vm_summary() {
  echo "VM ${VM_NAME} already exists (set RTRADER_VM_RECREATE=1 to rebuild)." | tee -a "$LOG_DIR/vm_setup.log"
  virsh dominfo "$VM_NAME" | tee -a "$LOG_DIR/vm_setup.log"
  echo "--- dumpxml summary ---" | tee -a "$LOG_DIR/vm_setup.log"
  virsh dumpxml "$VM_NAME" | grep -E 'memory|vcpu|on_reboot|source file|bus=|model type|bridge=' | head -20 | tee -a "$LOG_DIR/vm_setup.log"
}

mkdir -p "$INSTALLERS" "$VM_DIR" "$LOG_DIR" "$WATCH"
chmod o+x /root /root/hft3 "$INSTALLERS" 2>/dev/null || true

preflight_virt

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst genisoimage wget ovmf 2>&1 | tee -a "$LOG_DIR/vm_setup.log"

if [[ ! -f "$VIRTIO_ISO" ]]; then
  echo "Downloading virtio-win.iso..." | tee -a "$LOG_DIR/vm_setup.log"
  wget -q -O "$VIRTIO_ISO" "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
fi

if [[ ! -f "$WIN_ISO" ]]; then
  echo "ERROR: Windows ISO not found at $WIN_ISO" | tee -a "$LOG_DIR/vm_setup.log"
  echo "Upload from workstation: scp windows.iso chi404:${WIN_ISO}" | tee -a "$LOG_DIR/vm_setup.log"
  exit 1
fi

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

cp -f "$REPO/scripts/chi404_vm_guest_setup.ps1" "$WATCH/" 2>/dev/null || true
cp -f "$INSTALLERS/rithmic_portable.zip" "$WATCH/" 2>/dev/null || true
[[ -f "${WATCH}/rtrader_smb.env" ]] && cp -f "${WATCH}/rtrader_smb.env" "$INSTALLERS/rtrader_smb.env"

select_network_args

if [[ "$VM_RECREATE" == "1" ]]; then
  safe_recreate_vm
fi

if virsh dominfo "$VM_NAME" &>/dev/null; then
  print_existing_vm_summary
  ensure_on_reboot_restart
  virsh start "$VM_NAME" 2>/dev/null || true
else
  echo "Creating VM ${VM_NAME} (memory=${VM_MEMORY} vcpus=${VM_VCPUS} disk=${VM_DISK_SIZE}G bus=${VM_DISK_BUS} nic=${VM_NIC_MODEL} net=${NETWORK_MODE})..." | tee -a "$LOG_DIR/vm_setup.log"
  AUTOUNATTEND_ARGS=()
  if [[ -f "$AUTOUNATTEND_FLOPPY" ]]; then
    AUTOUNATTEND_ARGS=(--disk "path=${AUTOUNATTEND_FLOPPY},device=floppy")
    echo "Autounattend floppy attached: $AUTOUNATTEND_FLOPPY" | tee -a "$LOG_DIR/vm_setup.log"
  elif [[ -f "$AUTOUNATTEND_ISO" ]]; then
    AUTOUNATTEND_ARGS=(--disk "path=${AUTOUNATTEND_ISO},device=cdrom")
    echo "Autounattend ISO attached: $AUTOUNATTEND_ISO" | tee -a "$LOG_DIR/vm_setup.log"
  fi
  virt-install \
    --name "$VM_NAME" \
    --memory "$VM_MEMORY" \
    --vcpus "$VM_VCPUS" \
    --cpu host-passthrough \
    --disk "path=${DISK},size=${VM_DISK_SIZE},format=qcow2,bus=${VM_DISK_BUS}" \
    --cdrom "$WIN_ISO" \
    --disk "path=${VIRTIO_ISO},device=cdrom" \
    "${AUTOUNATTEND_ARGS[@]}" \
    --os-variant win2k22 \
    "${NETWORK_ARGS[@]}" \
    --graphics vnc,listen=127.0.0.1 \
    --noautoconsole \
    --boot cdrom,hd \
    --events on_reboot=restart
  ensure_on_reboot_restart
fi

virsh autostart "$VM_NAME" 2>/dev/null || true
echo "VM state: $(virsh domstate "$VM_NAME" 2>/dev/null || echo unknown)"
echo "Network mode: ${NETWORK_MODE:-unknown}"
echo "VNC: ssh -L 5900:127.0.0.1:5900 chi404 then connect to localhost:5900"
if [[ "$VM_DISK_BUS" == "virtio" ]]; then
  echo "VirtIO disk: during Windows install use Load driver -> virtio ISO -> viostor\\w10\\amd64 if no disk appears."
fi
