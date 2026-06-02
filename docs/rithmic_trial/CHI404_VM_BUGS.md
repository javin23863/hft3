# CHI404 Windows VM — historical bug log

> **Historical.** The R|Trader Windows VM was removed on 2026-06-02 in favor of the
> R|API+ native connector (see [RAPI_PLUS_HANDOFF_2026_06_02.md](../RAPI_PLUS_HANDOFF_2026_06_02.md)).
> The KVM / Windows / R|Trader install chain is no longer the trade path. This file is
> retained for forensic context only — **do not bring it back** without explicit user
> approval.

## What was on CHI404 (before 2026-06-02)

| Check | Result (2026-05-29) |
|-------|---------------------|
| `egrep -c 'vmx\|svm' /proc/cpuinfo` | **12** (AMD-V) |
| `/dev/kvm` | present, `crw-rw---- root kvm` |
| `lsmod \| grep kvm` | `kvm_amd`, `kvm` loaded |
| `libvirtd` | active |
| `virsh list --all` (before destroy) | `hft3-rtrader-win` runs |
| SMB share | `//192.168.122.1/rtrader_watch` (libvirt default NAT) |
| Capture service | `hft3-rithmic-trial.service` active, `RTRADER_START_WINE=0` |

The hardware (AMD-V, `/dev/kvm`) was fine. Failures during May 2026 were install
automation and config choices, not "machine can't VM."

## Removal (2026-06-02)

1. `virsh shutdown` was unresponsive (ACPI ignored by guest).
2. `virsh destroy` forced power-off.
3. `virsh undefine --remove-all-storage` removed qcow2 + 3 ISOs; autostart cleared.
4. `/var/lib/libvirt/images/hft3-rtrader/` was already empty and removed.
5. The 16 GB RAM + 4 vCPUs were reclaimed by `hft3-rithmic-trial.service`
   (CPUAffinity=2-11, MemoryHigh=8G, MemoryMax=12G) and the rest returned to host.

## Original bug log (preserved)

### 1. `virsh undefine --remove-all-storage` deleted ISOs

Once ran on CHI404 and removed `windows.iso` and other files under `/root/hft3/installers/`.
Server 2022 ISO was re-downloaded (~4.7 GB).

**Never** use `--remove-all-storage` on this domain.

### 2. Floppy `autounattend.xml` → license EULA error

Attaching autounattend via floppy caused: *"Windows cannot find the Microsoft Software
License Terms."* Floppy is **detached** in current flow.

### 3. Upgrade vs Custom

`Down` + `Enter` on install-type screen selects **Upgrade** → compatibility dialog →
dead end. Must **click** "Custom: Install … only (advanced)".

### 4. `on_reboot` was `destroy` (fixed on running domain)

Earlier XML had `<on_reboot>destroy</on_reboot>`, which dropped the guest on reboot
into WinRE. Recreate domain with `<on_reboot>restart</on_reboot>`.

### 5. VNC click coordinates vs keyboard

Mouse clicks at wrong coords silently no-op. **`Alt+N`** works reliably for Next on
setup dialogs. **`virsh send-key`** works when VNC session is idle; prefer
`vncdo key` for setup wizards.

### 6. `vncdo type` and OOBE password

Special characters and shift state break password confirmation. Use explicit field
clicks or finish password on console manually.

### 7. Wine path deprecated

Wine R|Trader on CHI404 fails (.NET / `mscoree`). Scripts moved to `scripts/deprecated/`.

### 8. Workstation log-push forbidden

`scripts/deprecated/push_rtrader_logs_chi404.ps1` and log-bridge scripts violated
`AGENTS.md` § topology. Capture must originate on CHI404.

### 9. `unattended.py` missing `import os`

Fixed: `RTRADER_START_WINE=0` guard needed `os.environ`.

### 10. No live R|Trader logs until guest setup completes

`/root/hft3/rtrader_watch/` had staged zip + `.ps1` + env files only. **`order_submit_ack`
speedtest** stayed `not_measured` until real `.log` files appeared.

## Why the VM is gone

User statement (2026-06-02): *"we had vm because no api now we have api no vm needed"*.
The R|API+ native connector is now the only trade path. See
[RAPI_PLUS_HANDOFF_2026_06_02.md §2](../RAPI_PLUS_HANDOFF_2026_06_02.md#2-current-trade-path)
for the current topology.
