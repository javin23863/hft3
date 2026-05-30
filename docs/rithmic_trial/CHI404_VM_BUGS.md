# CHI404 Windows VM — bug notes for next dev

Handoff from May 2026 KVM + R|Trader trial lane work. Canonical ops spec: **hft3_vm_modifications.pdf**. Read with [README.md](README.md) and `AGENTS.md` § topology (CHI404 only; no workstation capture).

## What works on CHI404 bare metal

| Check | Result (2026-05-29) |
|-------|---------------------|
| `egrep -c 'vmx\|svm' /proc/cpuinfo` | **12** (AMD-V) |
| `/dev/kvm` | present, `crw-rw---- root kvm` |
| `lsmod \| grep kvm` | `kvm_amd`, `kvm` loaded |
| `libvirtd` | active |
| `virsh list --all` | `hft3-rtrader-win` runs |
| SMB share | `//192.168.122.1/rtrader_watch` (libvirt default NAT) |
| Capture service | `hft3-rithmic-trial.service` active, `RTRADER_START_WINE=0` |

**Conclusion:** Hardware virtualization and KVM are fine. Failures were install automation and config choices, not “machine can’t VM.”

## VM config (current)

- **Name:** `hft3-rtrader-win`
- **Disk:** `/var/lib/libvirt/images/hft3-rtrader/hft3-rtrader-win.qcow2` (60 GB qcow2, **SATA** bus)
- **Network:** libvirt `default` NAT (`virbr0`), **e1000** (not VirtIO yet)
- **RAM / vCPU:** 8 GB / 4
- **ISOs:** `/root/hft3/installers/windows.iso` (Server 2022 eval), `virtio-win.iso` attached as 2nd CD
- **VNC:** `ssh -L 5900:127.0.0.1:5900 chi404` → `127.0.0.1:5900`
- **Guest IP (NAT):** `192.168.122.128/24` (when DHCP lease active)

### Gaps vs recommended production sidecar

| Item | Current | Target |
|------|---------|--------|
| Disk bus | SATA | VirtIO (`viostor` — Load driver during install) |
| NIC | e1000 + NAT | VirtIO + bridged/macvtap on `enp10s0f0np0` |
| Disk size | 60 GB | 100–120 GB |
| RAM | 8 GB | 16 GB |
| Boot | BIOS | UEFI + OVMF (installed on host) |
| `br0` | **does not exist** | Provider bridge or macvtap + spare colo IP |

Host default route: `64.44.98.219/25` on `enp10s0f0np0`. Do **not** re-plumb the primary NIC without a maintenance window.

## Install state (as of last session)

1. Manual VNC install **did run** — disk grew to ~**9.3 GB** (files copying).
2. Reached **“Customize settings”** (Administrator password). VNC `vncdo type` often fails password confirmation (`These passwords don't match`).
3. **Next step:** finish OOBE via VNC (type password carefully) or RDP after password is set; then run `chi404_vm_guest_setup.ps1` from SMB share `R:\`.

**Working VNC sequence** (1024×768, `vncdo -s 127.0.0.1::5900`):

```bash
# Language → Install now
vncdo key alt-n; sleep 3
vncdo key enter; sleep 8          # wait through "Setup is starting"

# Edition: Desktop Experience (2nd row) — NOT Server Core
vncdo key down; sleep 1
vncdo key alt-n; sleep 5

# EULA
vncdo key space; sleep 1
vncdo key alt-n; sleep 5

# Install type: Custom — double-click row; DO NOT Down+Enter (selects Upgrade → compatibility error)
vncdo move 512 430 click 1; sleep 1
vncdo move 512 430 click 1; sleep 5

# Disk: SATA sees "Drive 0 Unallocated Space" — Next
vncdo key alt-n; sleep 10
# Watch: du -h .../hft3-rtrader-win.qcow2  → should pass 500M within minutes
```

See `scripts/chi404_vm_vnc_install.sh` (updated in repo; old version was wrong on edition + Custom).

## Known bugs / footguns

### 1. `virsh undefine --remove-all-storage` deleted ISOs

Once ran on CHI404 and removed `windows.iso` and other files under `/root/hft3/installers/`. Server 2022 ISO was re-downloaded (~4.7 GB).

**Never** use `--remove-all-storage` on this domain.

### 2. Floppy `autounattend.xml` → license EULA error

Attaching autounattend via floppy caused: *“Windows cannot find the Microsoft Software License Terms.”* Floppy is **detached** in current flow. `infrastructure/chi404/autounattend.xml` kept for future fix (needs correct EULA / image index for Desktop Experience).

### 3. Upgrade vs Custom

`Down` + `Enter` on install-type screen selects **Upgrade** → compatibility dialog → dead end. Must **click** “Custom: Install … only (advanced)”.

### 4. `on_reboot` was `destroy` (fixed on running domain)

Earlier XML had `<on_reboot>destroy</on_reboot>`, which dropped the guest on reboot into WinRE “Choose an option”. Cold start (`virsh destroy` + `virsh start`) returns to ISO install. Recreate domain with `<on_reboot>restart</on_reboot>`.

### 5. VNC click coordinates vs keyboard

Mouse clicks at wrong coords silently no-op. **`Alt+N`** works reliably for Next on setup dialogs. **`virsh send-key`** works when VNC session is idle; prefer `vncdo key` for setup wizards.

### 6. `vncdo type` and OOBE password

Special characters and shift state break password confirmation. Use explicit field clicks or finish password on console manually. Set **`VM_ADMIN_PASSWORD`** in `/root/hft3/.env` to match the VM Administrator password (autounattend default: `Hft3Vm2026` — rotate in prod).

### 7. Wine path deprecated

Wine R|Trader on CHI404 fails (.NET / `mscoree`). Scripts moved to `scripts/deprecated/`. Do not retry unless explicitly requested.

### 8. Workstation log-push forbidden

`scripts/deprecated/push_rtrader_logs_chi404.ps1` and log-bridge scripts violate `AGENTS.md` § topology. Capture must originate on CHI404 from VM SMB logs only.

### 9. `unattended.py` missing `import os`

Fixed: `RTRADER_START_WINE=0` guard needs `os.environ`. Deployed to CHI404 `/root/hft3/repo`.

### 10. No live R|Trader logs until guest setup completes

`/root/hft3/rtrader_watch/` had staged zip + `.ps1` + env files only. **`order_submit_ack` speedtest** stays `not_measured` until real `.log` files appear and `pipeline process` writes trusted `latency_profile.json`.

## After Windows boots — checklist

```bash
# On CHI404
virsh domifaddr hft3-rtrader-win
# RDP: tunnel 3389 if enabled in guest, or VNC for first setup

# In guest (PowerShell as Admin), from R:\
powershell -ExecutionPolicy Bypass -File R:\chi404_vm_guest_setup.ps1

# Confirm logs land on host
find /root/hft3/rtrader_watch -name '*.log' -mmin -5

# Capture + process
systemctl status hft3-rithmic-trial
python3 -m data_system.rithmic_trial.pipeline process \
  --config data_system/config/rithmic_trial.yaml \
  --date $(date -u +%F) --symbol MES

# Speedtest from workstation (probe only)
# .\scripts\run_roundtrip_speedtest.ps1 --remote chi404 --samples 20
```

## Recreate VM with VirtIO defaults

[`11_rtrader_windows_vm.sh`](../../infrastructure/chi404/11_rtrader_windows_vm.sh) uses VirtIO disk/NIC, 16 GB RAM, 120 GB disk by default. Override via `RTRADER_VM_*` in `/root/hft3/.env`. Bridge `br0` is used when present; otherwise NAT fallback.

```bash
export RTRADER_VM_RECREATE=1
bash /root/hft3/repo/infrastructure/chi404/11_rtrader_windows_vm.sh
```

Never use `virsh undefine --remove-all-storage` (deletes ISOs under `/root/hft3/installers/`).

During install with VirtIO disk: **Load driver** → virtio ISO → `viostor\w10\amd64` (or matching folder). Install VirtIO NetKVM after first boot.

**Install-time SATA fallback:** set `RTRADER_VM_DISK_BUS=sata` if VNC cannot click Load driver.

**NAT NIC:** script uses **e1000** on NAT until guest NetKVM is installed (`RTRADER_VM_NIC_MODEL=virtio` on bridged `br0` only).

**Post-install (required):**

```bash
bash /root/hft3/repo/scripts/chi404_vm_boot_disk.sh   # eject windows.iso, boot hd
bash /root/hft3/repo/scripts/chi404_vm_oobe_offline.sh # optional: blank pwd + skip OOBE
# Or finish OOBE in VNC: set password to match `VM_ADMIN_PASSWORD` in `/root/hft3/.env`
bash /root/hft3/repo/scripts/chi404_vm_complete_install.sh  # language→custom→disk automation
```

Helper scripts: `chi404_vm_fix_nic.sh`, `chi404_vm_boot_disk.sh`, `chi404_vm_oobe_offline.sh`, `chi404_vm_complete_install.sh`.

## Provider ticket (only if `/dev/kvm` missing)

Not needed today. Template if colo changes:

```text
Please enable hardware virtualization in BIOS/IPMI:
- AMD-V / Intel VT-x
- IOMMU if available
Confirm /dev/kvm exists in Linux.
```
