# CHI404 CPU + memory overclock (operator)

**Goal:** Run DDR5 at **4800 MT/s** (EXPO) and raise Ryzen 9 9900X boost toward **~5.6–5.7 GHz** under load while keeping jitter PASS and broker-latency stable during RTH.

**Board:** ASRockRack **B650D4U-2L2T/BCM** — BMC at **`10.10.91.93`** (in-band VLAN, reachable from the host only while Linux + SSH are up). **Never reboot to BIOS without a proven out-of-band recovery path** (see [OOB requirement](#oob-requirement-never-reboot-to-bios-blind) below).

### Remote from Cambodia (or anywhere)

```powershell
# Keep running before any BIOS reboot — both tunnels
powershell -File scripts/run_chi404_bmc_ikvm_tunnel.ps1   # https://localhost:8443
powershell -File scripts/run_chi404_bmc_ipmi_tunnel.ps1   # IPMI localhost:1623
powershell -File scripts/run_chi404_oob_preflight_remote.ps1 -ProbeLocalTunnel
```

On CHI404 first (creates restore point + OOB gate):

```bash
bash infrastructure/chi404/17a_oob_preflight.sh
bash infrastructure/chi404/17_remote_bios_prep.sh
# Only after tunnels verified on workstation:
# HFT3_OOB_CONFIRMED=1 HFT3_BIOS_BOOT_NEXT=1 HFT3_BIOS_REBOOT=1 bash infrastructure/chi404/17_remote_bios_prep.sh
```

**Not scriptable from Linux alone:** EXPO profile toggle, PBO, voltage, and memory training are **UEFI-only**. Redfish can set CBS target speed but does **not** replace EXPO. Scripts verify and stress-test **after** each BIOS visit.

**Quarantined:** `22b_apply_and_reboot_bios.sh` — caused 2026-05-31 outage (reboot to BIOS with no OOB). Use `25_expo_sol_preflight.sh` instead.

**Recovery if stuck in BIOS:** `bash infrastructure/chi404/24_recover_boot_to_disk.sh` or from workstation: `powershell -File scripts/run_chi404_recover_remote.ps1`

Authority: [HARDWARE_BASELINE.md](HARDWARE_BASELINE.md) · [CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md)

---

## OOB requirement (never reboot to BIOS blind)

CHI404 BMC is **in-band** (`10.10.91.93`). The public IP exposes **no** BMC ports. When the host is in BIOS setup, **SSH is down** and Cambodia cannot reach iKVM/IPMI unless:

| Prerequisite | Check |
|--------------|-------|
| Host OOB preflight | `bash infrastructure/chi404/17a_oob_preflight.sh` → `OOB_PREFLIGHT=PASS` |
| iKVM tunnel live | `https://localhost:8443` returns HTTP 200 |
| IPMI tunnel live | `scripts/run_chi404_bmc_ipmi_tunnel.ps1` + `ipmitool` to `127.0.0.1:1623` |
| Explicit confirm | `HFT3_OOB_CONFIRMED=1` before `HFT3_BIOS_BOOT_NEXT=1` |
| Dedicated IPMI IP (future) | Channel 1 was `0.0.0.0` — ask colo to cable/enable `enp5s0`/`enp6s0` for OS-down recovery |

**If SSH does not return:** use **QuantVPS client portal IPMI/KVM** (works when OS is down). See [CHI404_ACCESS_PATHS.md](CHI404_ACCESS_PATHS.md).

```powershell
powershell -File scripts/run_chi404_oob_recovery.ps1
```

Ticket template: `runtime/chi404/quantvps_remote_hands_ticket.txt`

---

## Current vs target

| Item | Today (2026-05-31) | Target |
|------|-------------------|--------|
| DDR5 configured | **3600 MT/s** (JEDEC) | **4800 MT/s** (EXPO) |
| CPU max (cpupower table) | 4.40 GHz P-state | PBO boost **≥5.4 GHz** hot cores under load; single-core turbo toward **5.6–5.7 GHz** |
| SMT | Off (`nosmt`) | Keep **disabled** in BIOS + kernel |
| C-states | Disabled (GRUB + idle-set) | Keep disabled |

Micron `MB32G48U64M2R8.RsM` is rated **4800 MT/s** — 3600 means EXPO/XMP is off.

---

## Maintenance window procedure

### 0. Before touching BIOS

On CHI404:

```bash
cd /root/hft3/repo
bash infrastructure/chi404/14_bios_oc_readiness.sh
```

Note `RESTORE_ID` and `RUN_ID`. Rollback: [MEMORY_UPGRADE.md](MEMORY_UPGRADE.md) restore script if GRUB/kernel regress.

### 1. UEFI — memory first (4800 MT/s)

Use **iKVM via SSH tunnel** (see top of this doc) — not a physical visit.

| Menu (AMI, approximate) | Setting | Value |
|-------------------------|---------|--------|
| **DRAM Configuration** / **AMD CBS → DDR5** | EXPO / A-XMP | **Enabled** — select **4800** profile for `MB32G48U64M2R8.RsM` |
| | Memory speed | **4800 MT/s** (verify not 3600/5200 unless stable) |
| | Power-down / self-refresh | **Disabled** (match low-latency policy) |
| **Advanced → AMD Overclocking** | Precision Boost Overdrive | **Advanced** → **Motherboard limits** (or **Enabled** on first pass) |
| | PBO limits | Start **Motherboard** — do not max manual voltage on first visit |
| | Curve Optimizer | **Skip** until 4800 + jitter PASS |
| **Advanced → CPU Configuration** | SMT | **Disabled** |
| | Global C-state Control | **Disabled** |
| **Spread Spectrum** | | **Disabled** |
| **Memory pre-failure / SMI** | | **Disabled** (PDF §3.2 — see MEMORY_UPGRADE) |

Save & reboot. If POST fails: clear CMOS or revert EXPO → JEDEC, then try **4800 with relaxed timings** or one-DIMM test (colo hands).

### 2. Post-reboot verify (memory + CPU sample)

```bash
bash infrastructure/chi404/15_post_bios_oc_verify.sh
```

Must print **`OC_VERIFY=PASS`**. Failures:

- `memory_configured_mts < 4800` → re-enter BIOS, EXPO not applied
- `max_hot_cpu_mhz` below `HFT3_OC_MIN_MHZ` (default 5400) → enable PBO step 2 below

### 3. UEFI — CPU boost (iterative)

After **4800 PASS**, tune PBO in small steps. Log each profile in `runtime/chi404/oc/operator_log.jsonl` (append one JSON line per visit).

| Step | PBO change | Reboot | Verify |
|------|------------|--------|--------|
| A | PBO Advanced, limits **Motherboard** | Yes | `15_post_bios_oc_verify.sh` |
| B | +50 MHz PBO boost override (if exposed) | Yes | same |
| C | Curve Optimizer **−10** all cores (optional, stability) | Yes | same + jitter |

**Stop** if: POST fail, MCE in `dmesg`, `JITTER_GATE=FAIL`, or broker sweep error rate spikes.

Do **not** chase 5.7 GHz all-core — 9900X advertises **single-core** max boost; hot path uses **2–11** under load; gate on **minimum hot-core MHz** during stress and **jitter**, not idle `cpupower`.

### 4. Stability under market load

During **RTH** with broker session healthy:

```bash
export HFT3_OC_MARKET_LOAD=1
export HFT3_OC_RUN_BROKER_SWEEP=1  # optional: >=1000 order pairs
bash infrastructure/chi404/16_oc_stability_under_load.sh
```

Or from workstation:

```powershell
$env:HFT3_OC_MARKET_LOAD = "1"
$env:HFT3_OC_RUN_BROKER_SWEEP = "1"
.\scripts\run_chi404_oc_validate_remote.ps1 -Phase stability
```

Pass criteria:

- `OC_VERIFY=PASS` (memory ≥4800 MT/s, min hot-core MHz ≥5400 under stress)
- `JITTER_GATE=PASS` (p99 ≤ 20 µs on hot CPUs)
- Broker sweep completes (if enabled)
- No new MCE / EDAC errors in `dmesg`

### 5. Commit new baseline

```bash
bash infrastructure/chi404/00_hardware_baseline_capture.sh
# copy JSON → runtime/chi404/baseline/<timestamp>_baseline.json
```

Update [docs/ai/chi404_system_spec.json](../ai/chi404_system_spec.json) `known_gaps` — remove memory/ PBO gaps when verified.

---

## Rollback

1. BIOS: disable EXPO → JEDEC 3600, PBO **Disabled**
2. `RESTORE_ID=<id> bash infrastructure/chi404/00_restore_point_restore.sh --reboot` if kernel/GRUB touched
3. Re-run `01_fix_baseline_gaps.sh`

---

## Related

- [HARDWARE_BASELINE.md](HARDWARE_BASELINE.md)
- Broker latency: [CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md)
- Jitter gate: `infrastructure/chi404/05_jitter_gate.sh`
