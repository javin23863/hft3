# Rithmic Trial Ingestion Lane

Temporary bridge from R|Trader Pro (Wine on CHI404) into the quarantined HftBacktest replay workflow. Does **not** write to the trusted production data lake (`data/npz/` from Databento).

Spec: [rithmic_trial_hftbacktest_pipeline_prompt.pdf](../../rithmic_trial_hftbacktest_pipeline_prompt.pdf)

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

```bash
# Enable in config or env
export RITHMIC_TRIAL_ENABLED=1

python -m data_system.rithmic_trial.pipeline capture \
  --config data_system/config/rithmic_trial.yaml --force

python -m data_system.rithmic_trial.pipeline process \
  --date YYYY-MM-DD --symbol MES

python -m data_system.rithmic_trial.pipeline replay-sample \
  --npz data/replay/hftbacktest/rithmic_trial/YYYY-MM-DD/MES/MES_YYYY-MM-DD_trial.npz
```

## CHI404 only (Chicago colo)

Live Rithmic trial capture **must run on CHI404** — not on a Windows workstation. Millisecond latency cannot tolerate a remote desktop or file-bridge loop through your PC.

```bash
# On CHI404 (/root/hft3)
export RITHMIC_TRIAL_ENABLED=1

# Set in /root/hft3/.env
RTRADER_INSTALLER_PATH=/path/to/RTraderSetup.exe
RTRADER_WINE_PREFIX=/root/.wine-rtrader

bash infrastructure/chi404/08_rtrader_wine_setup.sh
bash scripts/setup_rithmic_chi404.sh
bash /root/hft3/logs/rtrader/launch_rtrader.sh

python -m data_system.rithmic_trial.pipeline run-unattended \
  --config data_system/config/rithmic_trial.yaml
```

First login may require provider console or VNC. Discovery output: `/root/hft3/logs/rtrader/rtrader_discovery.json` — copy `watch_dirs` into `data_system/config/rithmic_trial.yaml`.

Status file: `reports/rithmic_trial/YYYY-MM-DD/unattended_status.json`  
Log: `logs/rithmic_trial/unattended.log`

## Schema mapping

| Raw (R|Trader export/log) | Normalized v1 | HftBacktest NPZ |
|---------------------------|---------------|-----------------|
| trade / Time&Sales | `event_type=trade` | `TRADE_EVENT` rows |
| quote / BBO | `event_type=quote` | book hints only |
| order log | `order_submit`, `order_ack`, `fill` | not replayed (latency stats only) |
| depth (if available) | `event_type=depth` | **fail loudly** until R|API MBO mapper exists |

## Detected vs missing event types

After first live capture, see `manifest.json` → `detected_event_types` and `missing_event_types`.

Expected gaps via R|Trader bridge:

- Full MBO depth (ADD/CANCEL/MODIFY per order id)
- Sub-ms order hot-path latency (UI/manual path only)

## Latency numbers

`reports/rithmic_trial/YYYY-MM-DD/latency_profile.json`:

- **feed_latency_us** — `local_receive_timestamp_ns - exchange_timestamp_ns`
- **order_submit_to_ack_us** — paired order_submit / order_ack by `order_id`
- **order_rtt_ms** — average submit→ack in milliseconds

CHI404 CPU/network baseline: `infrastructure/03_latency_report.sh` → `latency_summary.json`

## R|API handoff (few days)

Swap **only** the input connector:

1. Implement `RithmicApiConnector` in [`data_system/rithmic_trial/connector/rithmic_api_connector.py`](../data_system/rithmic_trial/connector/rithmic_api_connector.py)
2. Set `connector: rithmic_api` in [`data_system/config/rithmic_trial.yaml`](../data_system/config/rithmic_trial.yaml)
3. Wire C++ [`rithmic_gateway/`](../../rithmic_gateway/) to emit the same raw NDJSON schema (optional)

**Unchanged downstream:** raw schema, normalized schema, validation, reports, HftBacktest converter, storage layout, research/backtest integration.

Do **not** put Wine or R|Trader paths in `rithmic_gateway/` C++ hot path.

## Config

[`data_system/config/rithmic_trial.yaml`](../data_system/config/rithmic_trial.yaml) — symbols, paths, connector selection. Override via env:

- `RITHMIC_TRIAL_ENABLED=1`
- `RITHMIC_TRIAL_CONNECTOR=fixture|rtrader|rithmic_api`
- `RITHMIC_SYMBOL`, `RITHMIC_EXCHANGE`
- `RTRADER_WINE_PREFIX`, `RTRADER_INSTALLER_PATH`
