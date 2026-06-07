# CHI404 hardware & runtime baseline (human)

**Purpose:** Single source of truth for what CHI404 **is today** — CPU, memory, kernel tuning, NIC, VM sidecar, hot-memory software layers, and how to **verify** nothing drifted before trading or tuning.

**AI mirror:** [docs/ai/chi404_system_spec.json](../ai/chi404_system_spec.json)  
**Latest snapshot:** [runtime/chi404/baseline/2026-05-31T030000Z_baseline.json](../../runtime/chi404/baseline/2026-05-31T030000Z_baseline.json)  
**Refresh on host:** `bash infrastructure/chi404/00_hardware_baseline_capture.sh`

Authority: [BLUEPRINT.md §4](../../BLUEPRINT.md#4-live-architecture) · [MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md)

---

## Host identity

| Field | Value (2026-05-31 audit) |
|-------|--------------------------|
| Hostname | `CHI404` |
| Role | Chicago colo bare metal — external broker Rithmic, capture, latency probes |
| Colo provider | **QuantVPS** |
| Public IP | `64.44.98.219/25` |
| OS | Ubuntu 22.04.5 LTS |
| Kernel | `5.15.0-179-generic` |
| Repo | `/root/hft3/repo` |
| Env | `/root/hft3/.env` (chmod 600, never commit) |

---

## CPU (bare metal)

| Item | Current state |
|------|----------------|
| Model | **AMD Ryzen 9 9900X** (12 physical cores) |
| SMT | **Off** (`nosmt`; CPUs 12–23 offline) |
| Logical CPUs | **12** (0–11) |
| Boost | **Enabled** — max **4.40 GHz** |
| Governor | **`performance`** |
| Virtualization | **AMD-V** (KVM for R\|Trader VM) |
| L3 cache | 64 MiB |

### CPU layout

| Role | CPU(s) | Purpose |
|------|--------|---------|
| OS | **0** | Kernel, ssh, systemd |
| Rithmic / NIC IRQ | **1** | `HFT3_RITHMIC_CPU` |
| HOT / isolated | **2–11** | Engine, cyclictest, cpuset `hft3-hot` |

**Drift:** ~~`.env` had stale `HFT3_ISOL_CPUS=2-23`~~ **Fixed 2026-05-31** — `.env` now `HOT_CPUS=2-11`, `HFT3_ISOL_CPUS=2-11`, `HFT3_NIC=enp10s0f0np0`.

---

## Memory (RAM)

| Item | Value |
|------|-------|
| Installed | **128 GiB** (4 × 32 GiB DDR5) |
| `free` reports | ~123 GiB (OS convention; use `total_installed_gib` in JSON for drift) |
| Part | Micron `MB32G48U64M2R8.RsM` |
| Rated | 4800 MT/s |
| **Configured** | **3600 MT/s** |
| ECC | None |
| Swap | 4 GiB |
| sysctl | `vm.swappiness=1`, `kernel.numa_balancing=0` |

### Kernel idle / C-state policy

| Mechanism | Setting |
|-----------|---------|
| GRUB | `processor.max_cstate=0`, `amd_idle.max_cstate=0`, `cpuidle.off=1` |
| GRUB idle | `idle=poll`, `rcu_nocb_poll`, `acpi_irq_nobalance` |
| Runtime | `cpupower idle-set -D 0` (**re-apply after reboot**) |
| Governor | `performance` |

Live cmdline (2026-05-31):

```
isolcpus=2-11 nohz_full=2-11 rcu_nocbs=2-11 isolcpus_managed_irq,domain
processor.max_cstate=0 amd_idle.max_cstate=0 cpuidle.off=1
mce=ignore_ce audit=0 nmi_watchdog=0 nosoftlockup nosmt
rcu_nocb_poll idle=poll acpi_irq_nobalance
```

---

## BIOS / UEFI (operator checklist)

Not readable from Linux. Log date + Y/N after each UEFI visit.

| Setting | Target |
|---------|--------|
| SMT | Disabled (matches `nosmt`) |
| C-states | Disabled or minimum |
| PBO / overclock | **Document exact profile** — see [CPU_MEMORY_OVERCLOCK.md](CPU_MEMORY_OVERCLOCK.md) |
| Memory EXPO/XMP | **Enable 4800 MT/s** — currently **3600 MT/s** |
| Memory pre-failure / SMI | Disabled for trading (PDF §3.2) |
| SVM | Enabled |

---

## Network

| Item | Value |
|------|-------|
| Primary NIC | `enp10s0f0np0` (`bnxt_en`) |
| RX / TX rings | **4096 / 2047** (hardware max TX 2047, RX 8191) |
| Offloads | **GRO/TSO/GSO off** (verified 2026-05-31) |
| Boot persistence | `hft3-net-tune.service` enabled |
| Rithmic | `ritpz04063.04.rithmic.com` |

**One-shot fix (workstation):** `powershell -File scripts/run_chi404_baseline_fix_remote.ps1`  
**On box:** `bash infrastructure/chi404/01_fix_baseline_gaps.sh`

**CPU/RAM overclock (UEFI + market-load validate):** [CPU_MEMORY_OVERCLOCK.md](CPU_MEMORY_OVERCLOCK.md)

---

## Windows VM (R\|Trader)

| Item | Current | Target |
|------|---------|--------|
| Name | `hft3-rtrader-win` | |
| RAM / vCPU | 16 GiB / 4 | same |
| Disk / NIC | SATA + e1000 NAT | VirtIO + bridge |

See [CHI404_VM_BUGS.md](../rithmic_trial/CHI404_VM_BUGS.md).

---

## Software hot layers

| Layer | Config / doc |
|-------|----------------|
| Market-state HOT/WARM | `apps/workbench/config/hot_memory_universe.yaml` · [HOT_MEMORY_UNIVERSE.md](../workbench/HOT_MEMORY_UNIVERSE.md) |
| C++ hot path | [MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md) |
| Macro E_t | `packages/economic_event_universe/config/event_universe.yaml` |

---

## Verify (testable)

1. **Baseline fix + capture:** `bash infrastructure/chi404/01_fix_baseline_gaps.sh` (or capture-only via `00_hardware_baseline_capture.sh`) → diff vs committed JSON  
2. **Full tuning PASS:** `run_chi404_tuning.sh` + `validate_pass_criteria.py`  
3. **Memory gap-fill:** [MEMORY_UPGRADE.md](MEMORY_UPGRADE.md) + `HFT3_VALIDATE_PROFILE=memory_upgrade`  
4. **Jitter:** cyclictest p99 ≤ **20 µs** on hot CPUs  
5. **Remote:** `bash scripts/run_chi404_validate_remote.sh`

---

## Change control

| Change | Before | After |
|--------|--------|-------|
| GRUB | restore point | reboot → validate → new baseline JSON |
| BIOS | operator log | baseline + cyclictest |
| IRQ/NIC | note RUN_ID | `04_irq_net_tuning.sh` + baseline |

Related: [MEMORY_UPGRADE.md](MEMORY_UPGRADE.md) · [CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md)
