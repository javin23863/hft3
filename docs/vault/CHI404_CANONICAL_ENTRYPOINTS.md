# CHI404 canonical entrypoints (agents: read before any CHI404 / Rithmic work)

**Graph first:** `graphify query "CHI404 R|API+ broker latency"` or read this doc.
**Do not** invent host-side log inject, workstation round-trips, or parallel orchestrators.

## Topology (non-negotiable)

Per [BLUEPRINT.md §4](../../BLUEPRINT.md#4-live-architecture): broker capture and order measurement run on **CHI404 bare metal** only.
Latency authority path: **R|API+ C++ adapter (in-process) → `rithmic_latency_probe` → JSONL/summary artifacts**.

The legacy capture daemon may still use `librithmic_gateway_shared.so` and Python ctypes for non-hot orchestration/capture plumbing. It is **not** the placement-speed authority. Order placement timing must come from the direct native C++ probe, with no Python broker wrapper in the measured path.

- No Windows VM.
- No R|Trader GUI.
- No SMB watch_dir.
- No `.cur.txt` ingest.

The `rtrader_bridge` connector (`RTraderBridgeConnector`) is **defensive legacy** for synthetic file-based ingest only. External broker R|API+ market-data capture is the `rithmic_api` connector. Order placement is not.

## Capture Daemon

The R|API+ market-data capture daemon is a single systemd unit on CHI404:

```bash
systemctl status hft3-rithmic-trial.service
journalctl -u hft3-rithmic-trial.service -n 50 --no-pager
ls -la /root/hft3/repo/runtime/rithmic_trial/rithmic_api.log
ls -la /root/hft3/repo/logs/rithmic_trial/unattended.log
```

Config (in `/root/hft3/.env`):

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `RITHMIC_TRIAL_ENABLED` | yes | unset | `1` to allow broker capture |
| `RITHMIC_TRIAL_CONNECTOR` | yes | — | `rithmic_api` (the only broker capture path) |
| `RITHMIC_TRIAL_CONFIG` | yes | — | `packages/data_system/config/rithmic_trial.yaml` |
| `HFT3_RITHMIC_GATEWAY_SO` | yes | — | `/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so` |
| `RITHMIC_USERNAME` / `RITHMIC_PASSWORD` | yes | — | Broker credentials; user-deployed, never committed |

## Broker capture (real market + order logs)

```bash
bash scripts/chi404_run_trial_capture.sh     # capture gate -> capture -> process -> replay
```

This script:

1. Verifies `hft3-rithmic-trial.service` is active.
2. Runs `python -m data_system.rithmic_trial.pipeline capture` (1-day raw NDJSON).
3. Runs `pipeline process` to produce normalized parquet.
4. Runs `pipeline replay-event --event-id $EVENT_ID` (Databento NPZ + hftbacktest 2.3.0).

Requires `EVENT_ID` (e.g. `CPI_2024_09_11_TIGHT`) for canonical research. Without it the script does a smoke replay only.

## Broker order submit→ack latency

**Forbidden:** `Add-Content`, host `f.write` order lines, `SWEEP-*` synthetic order IDs, TCP :65000 as ack.

Use the direct native C++ probe for placement-speed and submit-to-ack baselines:

```bash
cd /root/hft3/repo
cmake --build build --target rithmic_latency_probe --config Release
set -a; . /root/hft3/.env; set +a
RITHMIC_ENDPOINT_PROFILE=external_chicago \
RITHMIC_CONFIG_PATH=/root/hft3/repo/packages/data_system/config/rithmic_api_external.yaml \
RITHMIC_PROBE_ENV_LABEL=external \
RITHMIC_PROBE_SYMBOL=ESM6 \
RITHMIC_PROBE_EXCHANGE=CME \
RITHMIC_PROBE_ORDER_COUNT=30 \
RITHMIC_PROBE_CANCEL_AFTER_ACK=1 \
RITHMIC_PROBE_SKIP_MD=0 \
RITHMIC_PROBE_CPU=-1 \
RITHMIC_PROBE_RT_PRIORITY=0 \
RITHMIC_PROBE_MLOCK=1 \
RITHMIC_PROBE_PREFAULT_BYTES=16777216 \
./build/rithmic_gateway/rithmic_latency_probe
```

The accepted current baseline profile is no CPU affinity and no realtime
priority, with memory locking and 16 MiB prefault enabled. The realtime-priority
profile measured slower because it can starve Rithmic callback processing during
busy polling.

This writes:

| Artifact | Path |
|----------|------|
| Samples | `data/latency_baselines/YYYY-MM-DD/<run_id>.jsonl` |
| Summary | `reports/latency_baselines/<run_id>_summary.json` |
| Markdown | `reports/latency_baselines/<run_id>_summary.md` |

The compatibility sweep script now refuses Python/ctypes latency measurement and
prints this native-probe command shape:

```bash
bash scripts/chi404_run_broker_latency_sweep.sh
```

| Artifact | Path |
|----------|------|
| R|API+ SDK log | `runtime/rithmic_trial/rithmic_api.log` |
| Native baseline | `data/latency_baselines/YYYY-MM-DD/<run_id>.jsonl` |
| Summary | `reports/latency_baselines/<run_id>_summary.json` |

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
| `scripts/deprecated/chi404_run_broker_sweep_direct.sh` | Skips broker capture gate; session bypass |
| `RTraderBridgeConnector` for broker capture | VM is gone; use `rithmic_api` |
| `chi404_vm_*.{sh,py,ps1}` | VM chain is removed; do not resurrect |
| Workstation capture / log-push | BLUEPRINT §4 |
| `chi404_vm_broker_order_sweep.ps1` with `Add-Content` | Fake orders — blocked by pytest |

## Agent checklist (before editing CHI404 scripts)

1. `scripts/graphify_gate.sh -Query "..."` on CHI404 (no `.ps1` — the Windows bridge is gone)
2. Read this doc + [docs/rithmic_trial/README.md](../rithmic_trial/README.md) + [RAPI_PLUS_HANDOFF_2026_06_02.md](../RAPI_PLUS_HANDOFF_2026_06_02.md)
3. Prefer extending **existing** orchestrators — do not add parallel paths
4. `pytest tests/test_chi404_canonical_guardrails.py` after changes
5. Confirm the R|API+ SO is current: `stat /root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so`
