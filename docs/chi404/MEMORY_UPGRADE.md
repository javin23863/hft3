# CHI404 memory upgrade (PDF §2–3 gap-fill)

Bare-metal memory/kernel gap-fill for CHI404. **Does not** re-run the full [run_chi404_tuning.sh](../../infrastructure/chi404/run_chi404_tuning.sh) pipeline.

Authority: [MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md) · PDF [ultra_low_latency_hft_vector_search_architecture.pdf](../references/ultra_low_latency_hft_vector_search_architecture.pdf) §2–3

## When to use

CHI404 is **partially tuned** (isolcpus, C-state boot params, IRQ, cpuset already applied). This path adds only **missing** PDF §3 items:

| Gap | Action |
|-----|--------|
| `rcu_nocb_poll` | Append to GRUB if absent |
| `idle=poll` | Append if absent (skip with `HFT3_SKIP_IDLE_POLL=1`) |
| `acpi_irq_nobalance` | Append if absent |
| `cpupower idle-set -D 0` | Runtime MSR idle disable |
| `intel_idle.max_cstate=0` | Intel hosts only |

**Not in scope:** C++ §4–6, SmartNIC/DPDK, full tuning re-run.

## Procedure

### 1. From workstation (recommended)

```powershell
.\scripts\run_chi404_memory_upgrade_remote.ps1
```

After GRUB reboot (if prompted):

```powershell
$env:HFT3_MEMORY_RESUME_STEP=4
$env:RUN_ID=<printed-run-id>          # required — same RUN_ID as first invocation
$env:RESTORE_ID=<printed-restore-id>  # optional if RESTORE_ID.txt exists in log dir
.\scripts\run_chi404_memory_upgrade_remote.ps1
```

Resume step **4** re-applies `cpupower idle-set -D 0` (lost on reboot), then jitter + validate.

### 2. On CHI404 directly

```bash
cd /root/hft3/repo/infrastructure/chi404
chmod +x *.sh
bash run_chi404_memory_upgrade.sh
```

Logs: `/root/hft3/logs/memory_upgrade/<RUN_ID>/`  
Restore snapshot: `/root/hft3/restore_points/<RESTORE_ID>/`

## Rollback

```bash
RESTORE_ID=<id> bash /root/hft3/repo/infrastructure/chi404/00_restore_point_restore.sh --reboot
```

Restore **never** runs gap-fill. Use `--reboot` when GRUB was changed.

Runtime `cpupower idle-set` is re-applied automatically on post-reboot resume (step 4) and on restore when the snapshot recorded `idle_disabled_at_capture: true`.

## Manual BIOS checklist (PDF §3.2)

Operator must verify in UEFI/BIOS (not scriptable):

- Disable memory pre-failure / correctable ECC reporting that triggers SMI during trading windows
- Performance / C-states disabled at firmware level where available

Log completion in the run’s operator notes.

## Validation

After gap-fill (post-reboot if GRUB changed):

- `05_jitter_gate.sh` → `JITTER_GATE=PASS`
- `validate_pass_criteria.py` with `HFT3_VALIDATE_PROFILE=memory_upgrade` (skips IRQ/NIC/manifest gates; **requires** gap-fill cmdline tokens + disabled idle states)

## Idempotency

Re-running gap-fill on an already-upgraded host logs `SKIP already present` for each token and leaves GRUB unchanged (`MEMORY_GRUB_CHANGED=0`).

## Related

- Full colo tuning: [infrastructure/chi404/run_chi404_tuning.sh](../../infrastructure/chi404/run_chi404_tuning.sh)
- **Hardware baseline (CPU/RAM/NIC/BIOS):** [HARDWARE_BASELINE.md](HARDWARE_BASELINE.md)
- Remote tuning: [scripts/run_chi404_tuning_remote.ps1](../../scripts/run_chi404_tuning_remote.ps1)
- Topology: [BLUEPRINT.md](../../BLUEPRINT.md) §4
