# CHI404 baseline snapshots

Committed JSON captures of **live** CHI404 hardware/kernel/NIC state. Use for drift detection before tuning or trading windows.

| File | When |
|------|------|
| `2026-05-31T030000Z_baseline.json` | Initial full audit (Ryzen 9900X, 128 GiB DDR5 @ 3600 MT/s, cmdline tuned) |

## Refresh

On CHI404:

```bash
cd /root/hft3/repo
bash infrastructure/chi404/00_hardware_baseline_capture.sh
```

Copy the new `baseline.json` here (or diff locally):

```bash
diff -u runtime/chi404/baseline/2026-05-31T030000Z_baseline.json \\
  /root/hft3/logs/hardware_baseline/<RUN_ID>/baseline.json
```

Commit when intentional (BIOS change, tuning PASS, memory upgrade).

**Human doc:** [docs/chi404/HARDWARE_BASELINE.md](../../docs/chi404/HARDWARE_BASELINE.md)  
**AI doc:** [docs/ai/chi404_system_spec.json](../../docs/ai/chi404_system_spec.json)
