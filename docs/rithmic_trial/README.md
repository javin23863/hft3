# Rithmic Trial Ingestion Lane

Temporary bridge from R|Trader Pro (Windows VM on CHI404) into the quarantined HftBacktest replay workflow. Does **not** write to the trusted production data lake (`data/npz/` from Databento).

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

**Supported path:** native R|Trader in a KVM Windows VM; logs export via SMB to `/root/hft3/rtrader_watch` → `RTraderBridgeConnector` → `hft3-rithmic-trial.service`.

```bash
# On CHI404 (/root/hft3)
export RITHMIC_TRIAL_ENABLED=1

# One-time host setup (SMB + VM + capture bridge)
# Rebuild VM with VirtIO defaults: export RTRADER_VM_RECREATE=1 first
bash /root/hft3/repo/scripts/chi404_finish_rtrader.sh

# Or VM only (see hft3_vm_modifications.pdf / CHI404_VM_BUGS.md):
# export RTRADER_VM_RECREATE=1
# bash /root/hft3/repo/infrastructure/chi404/11_rtrader_windows_vm.sh

# Requires /root/hft3/installers/windows.iso (upload once from workstation if missing)
# Windows install console: ssh -L 5900:127.0.0.1:5900 chi404 → VNC localhost:5900
# RDP after guest setup: ssh -L 3389:192.168.122.128:3389 chi404

# Env in /root/hft3/.env (defaults in .env.example)
RTRADER_VM_MEMORY=16384
RTRADER_VM_DISK_SIZE=120
RTRADER_VM_DISK_BUS=virtio
RTRADER_VM_NIC_MODEL=virtio
RTRADER_VM_BRIDGE_NAME=br0
RTRADER_WATCH_DIRS="/root/hft3/rtrader_watch"
RTRADER_START_WINE=0
RITHMIC_TRIAL_CONNECTOR=rtrader
RTRADER_SMB_USER=rtrader
RTRADER_SMB_HOST=192.168.122.1
```

Guest VM uses **headless autostart** after first install: `chi404_vm_apply_headless.py` registers auto-logon + scheduled tasks (MapSMB → R|Trader → login). Logs land via `Documents\Rithmic` symlink to `//192.168.122.1/rtrader_watch`. **VNC/RDP only for first-time install or recovery.**

```bash
# On CHI404 after Windows is installed
export VM_ADMIN_PASSWORD='...'   # must match VM Administrator password
python3 /root/hft3/repo/scripts/chi404_vm_apply_headless.py
virsh autostart hft3-rtrader-win

# Health check (no VNC)
python3 /root/hft3/repo/scripts/chi404_vm_status_check.py
```

Guest first boot can also run `scripts/chi404_vm_guest_setup.ps1` (VirtIO drivers, R|Trader zip, SMB symlink, legacy logon task). Prefer `chi404_vm_apply_headless.py` for production sidecar reboots.

**VM install handoff / known bugs:** [CHI404_VM_BUGS.md](CHI404_VM_BUGS.md)

Verification gates (run once each):

```bash
virsh domstate hft3-rtrader-win          # running
find /root/hft3/rtrader_watch -name '*.log'
systemctl is-active hft3-rithmic-trial     # active
cat reports/rithmic_trial/$(date -u +%Y-%m-%d)/unattended_status.json
```

### Deprecated: Wine on Linux

Wine + dotnet472 on CHI404 **does not work** for R|Trader (.NET/mscoree). Scripts moved to `scripts/deprecated/`. Do not use workstation log-push (`push_rtrader_logs_chi404.ps1`) — forbidden by AGENTS.md topology.

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
- `RTRADER_WINE_PREFIX`, `RTRADER_INSTALLER_PATH` (Wine path — deprecated)
- `RTRADER_WATCH_DIRS`, `RTRADER_START_WINE=0`, `RTRADER_SMB_*` (VM + SMB path)
