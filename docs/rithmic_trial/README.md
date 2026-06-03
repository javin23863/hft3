# Rithmic Trial Ingestion Lane

Live R|API+ capture on CHI404 into the quarantined HftBacktest replay workflow. Does **not** write to the trusted production data lake (`data/npz/` from Databento).

Spec: [rithmic_trial_hftbacktest_pipeline_prompt.pdf](../../rithmic_trial_hftbacktest_pipeline_prompt.pdf)
Handoff: [RAPI_PLUS_HANDOFF_2026_06_02.md](../RAPI_PLUS_HANDOFF_2026_06_02.md)
Diamond / Diamond Cutter access packet: [DIAMOND_CUTTER_ACCESS_SAMPLE.md](DIAMOND_CUTTER_ACCESS_SAMPLE.md)

## Topology

CHI404 bare metal only. R|API+ C++ adapter (in-process) loads `librithmic_gateway_shared.so`, exposes a ctypes surface, and is consumed by `RithmicApiConnector`. There is no Windows VM, no R|Trader GUI, and no SMB watch dir.

```
[ R|API+ adapter C++ ]  ->  librithmic_gateway_shared.so  ->  Python ctypes (RithmicApiConnector)
        ->  hft3-rithmic-trial.service (systemd, pinned to CPUs 2-11)
                ->  runtime/rithmic_trial/  (R|API+ SDK log)
                ->  logs/rithmic_trial/unattended.log
                ->  data/raw/rithmic_trial_live_capture/.../events.ndjson
                ->  data/normalized/.../events.ndjson (normalized_v1)
                ->  data/replay/hftbacktest/rithmic_trial/.../SYMBOL_YYYY-MM-DD_trial.npz
```

## Storage layout

```
data/raw/rithmic_trial_live_capture/YYYY-MM-DD/SYMBOL/
  events.ndjson          # append-only raw capture
  manifest.json          # checksum, counts, detected/missing types

data/normalized/rithmic_trial_live_capture/YYYY-MM-DD/SYMBOL/
  events.ndjson          # normalized_v1 schema

data/replay/hftbacktest/rithmic_trial/YYYY-MM-DD/SYMBOL/
  SYMBOL_YYYY-MM-DD_trial.npz

reports/rithmic_trial/YYYY-MM-DD/
  data_capture_report.json
  schema_mapping_report.json
  data_quality_report.json
  book_reconstruction_report.json
  latency_profile.json
  hftbacktest_conversion_report.json
```

## Pipeline commands

Macro event replay (Databento NPZ + CHI404 latency — **use for CPI/NFP research**):

```bash
python -m data_system.rithmic_trial.pipeline replay-event \
  --event-id CPI_2024_09_11_TIGHT \
  --config data_system/config/rithmic_trial.yaml
```

Trial ingest (capture session date = folder `YYYY-MM-DD`, not macro event):

```bash
export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rithmic_api
export RITHMIC_TRIAL_CONFIG=packages/data_system/config/rithmic_trial.yaml
export HFT3_RITHMIC_GATEWAY_SO=/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so

python -m data_system.rithmic_trial.pipeline capture \
  --config data_system/config/rithmic_trial.yaml --force --event-id CPI_2024_09_11_TIGHT

python -m data_system.rithmic_trial.pipeline process \
  --date YYYY-MM-DD --symbol MES
```

CHI404 live (recommended):

```bash
EVENT_ID=CPI_2024_09_11_TIGHT bash scripts/chi404_run_trial_live.sh
```

**Smoke only** (trial NPZ wiring check — not macro research):

```bash
python -m data_system.rithmic_trial.pipeline replay-sample \
  --npz data/replay/hftbacktest/rithmic_trial/YYYY-MM-DD/MES/MES_YYYY-MM-DD_trial.npz \
  --simple
```

See [docs/vault/RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) and [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md) for the canonical order.

## CHI404 only (Chicago colo)

Live Rithmic trial capture **must run on CHI404** — not on a Windows workstation. Millisecond latency cannot tolerate a remote desktop or file-bridge loop through your PC.

**Supported path:** native R|API+ adapter (libRApiPlus 13.7.0.0), consumed via the `librithmic_gateway_shared.so` ctypes bridge.

```bash
# On CHI404 (/root/hft3)
export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rithmic_api
export RITHMIC_TRIAL_CONFIG=packages/data_system/config/rithmic_trial.yaml
export HFT3_RITHMIC_GATEWAY_SO=/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so

# Idempotent: reapply pin + cpuset on reboot
bash infrastructure/chi404/06_cpuset_systemd.sh
bash infrastructure/chi404/09_rithmic_trial_systemd.sh

systemctl status hft3-cpuset.service
systemctl status hft3-rithmic-trial.service
```

Verification gates (run once each):

```bash
systemctl is-active hft3-rithmic-trial              # active
journalctl -u hft3-rithmic-trial -n 50 --no-pager
ls /root/hft3/repo/runtime/rithmic_trial/          # SDK log
ls /root/hft3/repo/data/raw/rithmic_trial_live_capture/$(date -u +%F)/MES/
```

`RITHMIC_USERNAME` / `RITHMIC_PASSWORD` (paper trading) live in `/root/hft3/.env`. They are user-deployed and **must not be committed**.

### Paper order submit→ack latency (authoritative)

TCP connect probes (`network.rithmic_tcp_65000`) are **network health only** — they do not set replay or workbench `gateway_ack`.

**Canonical agent doc:** [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md)

Measured paper submit→ack requires ≥1,000 **real** paired orders via the R|API+ connector (`RithmicApiConnector.send_order` + matching `order_ack`). **Synthetic log inject (`Add-Content`, host `f.write`) is forbidden** and blocked by pytest.

```bash
# Full sweep (live gate -> daemon -> wait for paired>=1000 -> promote -> latency_summary)
bash scripts/chi404_run_paper_latency_sweep.sh

# Or connectivity / daemon plumbing check, no real orders:
PAPER_LATENCY_SKIP_ORDERS_BURST=1 bash scripts/chi404_run_paper_latency_sweep.sh
```

**Known gap (2026-06-02, RAPI+ handoff §3):** the R|API+ order callbacks (`orderSubmit` / `orderAck`) are not yet wired into the SPSC queue the daemon polls. Until that wiring lands, `paired_submit_ack_count` stays at 0 and the orchestrator times out. Use `PAPER_LATENCY_SKIP_ORDERS_BURST=1` to verify everything else.

Do **not** use deprecated host-side sweep scripts under `scripts/deprecated/`.

Artifacts:

```
runtime/paper_latency/raw/<run_id>/records.ndjson   # monotonic audit
reports/rithmic_trial/<date>/latency_waterfall.json
reports/rithmic_trial/<date>/paper_order_summary.json
runtime/latency_reports/latency_summary.json        # order_ack_p99_ms when promoted
```

Refresh probe summary after sweep:

```bash
python3 scripts/latency_probe/summarize_latency.py --run-id <probe_run_id> --include-trial-appendix
```

Until `order_ack_measured=true`, macro replay requires explicit `--latency-ms`.

## Schema mapping

| Raw (R|API+ adapter) | Normalized v1 | HftBacktest NPZ |
|----------------------|---------------|-----------------|
| `market_event` (trade/quote) | `event_type=trade\|quote` | trade → `TRADE_EVENT` rows, quote → book hints only |
| `depth_event` (MBO) | `event_type=depth` | not yet mapped; book_reconstruction_report flags gap |
| `order_submit` | `event_type=order_submit` | not replayed (latency stats only) |
| `order_ack` | `event_type=order_ack` | latency stats only |
| `fill` | `event_type=fill` | latency stats only |
| `cancel` / `order_replace` | same | latency stats only |

## Detected vs missing event types

After first live capture, see `manifest.json` → `detected_event_types` and `missing_event_types`.

Expected gaps via R|API+:

- Full MBO depth (ADD/CANCEL/MODIFY per order id) — requires depthEvent callback wiring
- Sub-ms order hot-path latency (depends on RithmicS2/line + clock sync)

## Latency numbers

`reports/rithmic_trial/YYYY-MM-DD/latency_profile.json`:

- **feed_latency_us** — `local_receive_timestamp_ns - exchange_timestamp_ns`
- **order_submit_to_ack_us** — paired order_submit / order_ack by `order_id`
- **order_rtt_ms** — average submit→ack in milliseconds

CHI404 CPU/network baseline: `infrastructure/03_latency_report.sh` → `latency_summary.json`

## Config

[`data_system/config/rithmic_trial.yaml`](../data_system/config/rithmic_trial.yaml) — symbols, paths, connector selection. Override via env:

- `RITHMIC_TRIAL_ENABLED=1`
- `RITHMIC_TRIAL_CONNECTOR=fixture|rithmic_api` (only `rithmic_api` is the live path on CHI404; `rtrader` is defensive legacy)
- `RITHMIC_TRIAL_CONFIG` — config YAML
- `HFT3_RITHMIC_GATEWAY_SO` — path to `librithmic_gateway_shared.so` (no silent fallback)
- `RITHMIC_SYMBOL`, `RITHMIC_EXCHANGE`
- `RITHMIC_USERNAME`, `RITHMIC_PASSWORD` — paper creds; live in `/root/hft3/.env`
- `HFT3_RITHMIC_GATEWAY_*` — SSL cert, MML_LOG_TYPE, MML_LOG_ADDR (UAT defaults; port 45454 may be firewalled by Rithmic — see RAPI+ handoff)

## Historical / removed

The following paths existed for the now-removed R|Trader Windows VM and are no longer maintained. They are not on the live trade path:

- `scripts/chi404_vm_*.{sh,py,ps1}` — VM deploy / restart / SMB / log path
- `infrastructure/chi404/08_rtrader_wine_setup.sh`
- `infrastructure/chi404/10_rtrader_smb_share.sh`
- `infrastructure/chi404/11_rtrader_windows_vm.sh`
- `infrastructure/chi404/autounattend.xml`

See [CHI404_VM_BUGS.md](CHI404_VM_BUGS.md) for the historical bug log.
