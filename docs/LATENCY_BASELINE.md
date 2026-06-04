# Latency Baseline

This baseline measures the system's placement speed separately from broker or exchange acknowledgment latency.

Placement speed answers:

```text
How fast did the system react to a market event and send an order?
```

Round-trip acknowledgment latency answers:

```text
How long did the broker or venue path take to acknowledge a sent order, cancel, or replace?
```

Those are different numbers. The primary KPI for this module is `tick_to_send_us`; `send_to_ack_us` is reported separately and must not be described as placement speed.

## Metrics

| Metric | Definition | View |
| --- | --- | --- |
| `tick_to_decision_us` | Market event received to decision produced | Offensive |
| `decision_to_send_us` | Decision produced to order sent by the application | Offensive |
| `tick_to_send_us` | Market event received to order sent by the application | Offensive, primary KPI |
| `send_to_ack_us` | Order sent to broker/exchange acknowledgment received | Round trip |
| `cancel_to_send_us` | Cancel decision produced to cancel sent | Defensive |
| `cancel_to_ack_us` | Cancel sent to cancel acknowledgment received | Defensive and round trip |
| `replace_to_send_us` | Replace decision produced to replace sent | Defensive |
| `replace_to_ack_us` | Replace sent to replace acknowledgment received | Defensive and round trip |

## Timestamp Probes

The recorder accepts high-resolution monotonic timestamps at these boundaries:

- `market_event_received_ts`
- `features_ready_ts`
- `decision_ready_ts`
- `risk_check_ready_ts`
- `order_ready_ts`
- `order_send_ts`
- `ack_received_ts`
- `cancel_send_ts`
- `cancel_ack_received_ts`
- `replace_send_ts`
- `replace_ack_received_ts`

Duration math uses monotonic nanoseconds. Wall-clock UTC is stored only as sample metadata.

For cancel and replace actions, `decision_ready_ts` represents the cancel or replace decision boundary. The action-specific send probes are `cancel_send_ts` and `replace_send_ts`.

## Outputs

Samples are written as JSONL:

```text
data/latency_baselines/YYYY-MM-DD/<run_id>.jsonl
```

Each run writes:

```text
reports/latency_baselines/<run_id>_summary.json
reports/latency_baselines/<run_id>_summary.md
reports/latency_baselines/<run_id>_capability.json
reports/latency_baselines/<run_id>_capability.md
```

The JSON and Markdown reports include min, mean, p50, p90, p95, p99, p99.9, max, and sample count for every latency metric.

The capability reports convert raw latency into operational statements for offensive, defensive, and hybrid model testing. They classify internal placement speed separately from external acknowledgment lag, describe pending-state risk, and explain whether the selected model interaction mode fits inside the configured opportunity window.

## Run Synthetic Mode

Synthetic mode verifies the recorder, duration math, JSONL output, summary reports, and baseline comparison without broker connectivity:

```powershell
python -m tools.latency_baseline.run --mode synthetic --duration 30
```

For fast local verification:

```powershell
python -m tools.latency_baseline.run --mode synthetic --duration 1 --samples 30 --run-id local-smoke
```

Synthetic output is labeled with the selected environment, broker, venue, and strategy metadata, but it is not broker evidence.

## Broker Mode

The broker command shape is:

```powershell
python -m tools.latency_baseline.run `
  --env paper `
  --broker rithmic `
  --symbol ES `
  --exchange CME `
  --duration 300 `
  --strategy latency_probe
```

For `--broker rithmic --env paper`, broker mode uses the configured Rithmic Paper/Chicago endpoint and sends a tagged passive limit-order probe. If `--symbol ES` is supplied, the runner resolves it to the current front-month contract before subscribing and sending. The default `latency_probe` derives a passive limit price from the first live quote/trade so the command can run without an explicit price.

Broker mode fails loudly with `BROKER_LATENCY_BASELINE_FAILED` when the endpoint, credentials, market data, or order acknowledgment path is unavailable. It does not fall back to synthetic samples.

The runner buffers raw Rithmic events in memory while measuring placement speed and flushes them after the send/ack path. This keeps JSONL persistence and report writing outside the measured hot path.

## Capability Modeling

The baseline command also accepts speed-aware testing assumptions:

```powershell
python -m tools.latency_baseline.run `
  --env paper `
  --broker rithmic `
  --symbol ES `
  --exchange CME `
  --duration 300 `
  --strategy latency_probe `
  --interaction-mode hybrid_configuration `
  --opportunity-decay-us 1000 `
  --competitor-tick-to-send-us 250 `
  --arbitration-latency-us 25 `
  --hybrid-coordination-latency-us 50 `
  --max-pending-orders 3
```

Supported interaction modes:

- `offensive_only`
- `defensive_always_active`
- `defensive_pre_action_only`
- `defensive_during_action`
- `defensive_post_action`
- `concurrent_offensive_defensive`
- `hybrid_configuration`

Capability modeling is nonblocking by default: when a probe order is sent, local state moves to `PENDING_NEW`; cancel and replace actions move to `PENDING_CANCEL` and `PENDING_REPLACE`. Acknowledgments reconcile official state asynchronously. The capability report flags stale-state risk, pending exposure limits, duplicate-order protection, client order ID tracking, and kill-switch requirements.

## Reading The Report

Use the three report views:

- Offensive: `tick_to_decision_us`, `decision_to_send_us`, and `tick_to_send_us`
- Defensive: cancel and replace send/ack timings
- Round Trip: order, cancel, and replace acknowledgment timings

The first number to inspect for model reaction speed is `tick_to_send_us`. The first number to inspect for broker or venue response behavior is `send_to_ack_us`.

## Baseline Comparison

The canonical baseline path is:

```text
reports/latency_baselines/current_baseline.json
```

Each run compares against this file when it exists. The comparison reports improvement, degradation, unchanged status, absolute microsecond change, and percentage change for p50, p99, and p99.9.

Default warning thresholds:

- p50 degradation greater than 10%
- p99 degradation greater than 15%
- p99.9 degradation greater than 20%
- hard fail if `tick_to_send_us` p99.9 degrades by more than 25%

## Updating The Canonical Baseline

After a run is accepted as the new authority, update the canonical baseline explicitly:

```powershell
python -m tools.latency_baseline.run --mode synthetic --run-id accepted-baseline --update-current-baseline
```

For broker evidence, only update `current_baseline.json` after the run is produced from real execution-boundary probes.
