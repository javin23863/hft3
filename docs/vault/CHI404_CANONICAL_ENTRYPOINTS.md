# CHI404 canonical entrypoints (agents: read before any CHI404 / Rithmic work)

**Graph first:** `graphify query "CHI404 R|API+ paper latency"` or read this doc.  
**Do not** invent host-side log inject, workstation round-trips, or parallel orchestrators.

## Topology (non-negotiable)

Per [BLUEPRINT.md §4](../../BLUEPRINT.md#4-live-architecture): live/paper capture and order measurement run on **CHI404 bare metal** only.  
Path: **R|API+ C++ adapter (in-process) → `librithmic_gateway_shared.so` → Python ctypes → capture / latency daemon**.

- No Windows VM.
- No R|Trader GUI.
- No SMB watch_dir.
- No `.cur.txt` ingest.

The `rtrader_bridge` connector (`RTraderBridgeConnector`) is **defensive legacy** for synthetic file-based ingest only. Live R|API+ traffic is `rithmic_api` connector.

## Trade-path daemon

The R|API+ capture + order-event daemon is a single systemd unit on CHI404:

```bash
systemctl status hft3-rithmic-trial.service
journalctl -u hft3-rithmic-trial.service -n 50 --no-pager
ls -la /root/hft3/repo/runtime/rithmic_trial/rithmic_api.log
ls -la /root/hft3/repo/logs/rithmic_trial/unattended.log
```

Config (in `/root/hft3/.env`):

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `RITHMIC_TRIAL_ENABLED` | yes | unset | `1` to allow live capture |
| `RITHMIC_TRIAL_CONNECTOR` | yes | — | `rithmic_api` (the only live path) |
| `RITHMIC_TRIAL_CONFIG` | yes | — | `packages/data_system/config/rithmic_trial.yaml` |
| `HFT3_RITHMIC_GATEWAY_SO` | yes | — | `/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so` |
| `RITHMIC_USERNAME` / `RITHMIC_PASSWORD` | yes | — | UAT (paper) creds; user-deployed, never committed |

## Live capture (real market + order logs)

```bash
bash scripts/chi404_run_trial_live.sh        # live gate → capture → process → replay
```

This script:

1. Verifies `hft3-rithmic-trial.service` is active.
2. Runs `python -m data_system.rithmic_trial.pipeline capture` (1-day raw NDJSON).
3. Runs `pipeline process` to produce normalized parquet.
4. Runs `pipeline replay-event --event-id $EVENT_ID` (Databento NPZ + hftbacktest 2.3.0).

Requires `EVENT_ID` (e.g. `CPI_2024_09_11_TIGHT`) for canonical research. Without it the script does a smoke replay only.

## Paper order submit→ack latency

**Forbidden:** `Add-Content`, host `f.write` order lines, `SWEEP-*` synthetic order IDs, TCP :65000 as ack.

```bash
bash scripts/chi404_run_paper_latency_sweep.sh
```

The orchestrator:

1. Runs an R|API+ reachability gate (connect, fetch `limitations()`).
2. Starts `paper_latency_daemon` (5-attempt retry-safe connect).
3. Waits for `paired_submit_ack_count >= PAPER_LATENCY_TARGET_ORDERS` (default 1000).
4. Promotes records to `reports/rithmic_trial/<date>/`.
5. Refreshes `latency_summary.json`.

**Known gap (2026-06-02, RAPI+ handoff §3):** the R|API+ order callbacks (`orderSubmit` / `orderAck`) are not yet wired into the SPSC queue the daemon polls. Until that wiring lands, the paired count stays at 0 and the orchestrator times out after ~20 min. To verify connectivity and the daemon plumbing without real orders:

```bash
PAPER_LATENCY_SKIP_ORDERS_BURST=1 bash scripts/chi404_run_paper_latency_sweep.sh
```

| Artifact | Path |
|----------|------|
| Raw audit | `runtime/paper_latency/raw/<run_id>/records.ndjson` |
| R|API+ SDK log | `runtime/rithmic_trial/rithmic_api.log` |
| Daemon log | `logs/rithmic_trial/unattended.log` |
| Trial reports | `reports/rithmic_trial/<date>/` |
| Summary | `runtime/latency_reports/latency_summary.json` |

Refresh probe summary:

```bash
python3 scripts/latency_probe/summarize_latency.py --run-id <probe_run_id> --include-trial-appendix
```

## One-time / recovery (host pin + cpuset)

After reboot, cgroup v2 children don't survive. Recreate `/sys/fs/cgroup/hft3-hot`:

```bash
systemctl status hft3-cpuset.service hft3-rithmic-trial.service
bash infrastructure/chi404/06_cpuset_systemd.sh       # idempotent install
bash infrastructure/chi404/09_rithmic_trial_systemd.sh # idempotent
```

These units pin the daemon to CPUs 2-11, 8 GB memory high / 12 GB max, and recreate the cgroup cpuset on boot.

## Deprecated / forbidden paths

| Path | Why forbidden |
|------|----------------|
| `scripts/deprecated/chi404_*host*sweep*` | Host-side synthetic log inject |
| `scripts/deprecated/chi404_*fast_market*` | Host-side synthetic log inject |
| `scripts/deprecated/chi404_run_paper_sweep_direct.sh` | Skips live gate; session bypass |
| `RTraderBridgeConnector` for live capture | VM is gone; use `rithmic_api` |
| `chi404_vm_*.{sh,py,ps1}` | VM chain is removed; do not resurrect |
| Workstation capture / log-push | BLUEPRINT §4 |
| `chi404_vm_paper_order_sweep.ps1` with `Add-Content` | Fake orders — blocked by pytest |

## Agent checklist (before editing CHI404 scripts)

1. `scripts/graphify_gate.sh -Query "..."` on CHI404 (no `.ps1` — the Windows bridge is gone)
2. Read this doc + [docs/rithmic_trial/README.md](../rithmic_trial/README.md) + [RAPI_PLUS_HANDOFF_2026_06_02.md](../RAPI_PLUS_HANDOFF_2026_06_02.md)
3. Prefer extending **existing** orchestrators — do not add parallel paths
4. `pytest tests/test_chi404_canonical_guardrails.py` after changes
5. Confirm the R|API+ SO is current: `stat /root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so`
