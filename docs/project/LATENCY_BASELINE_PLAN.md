# Permanent Latency Baseline Plan

## Objective

Add a permanent latency baseline module to the repository that measures the system's true order-placement speed separately from broker/exchange acknowledgment latency.

## Purpose

Create a repeatable benchmark that tells us how fast the system can react to market data, make a decision, pass risk checks, build an order, and send it. This baseline will be used to evaluate offensive models, defensive models, trade manager behavior, queue tactics, and future execution improvements.

## Core Principle

Do not confuse round-trip latency with placement speed.

Placement speed is:

```text
Market event received -> order send path triggered
```

Round-trip latency is:

```text
Order sent -> acknowledgment received
```

Both must be measured, but they must be reported separately.

## Required Metrics

Measure and report:

1. Tick-to-Decision: market data event received -> decision produced
2. Decision-to-Send: decision produced -> order sent from the application
3. Tick-to-Send: market data event received -> order sent from the application
4. Send-to-Ack: order sent -> broker/exchange acknowledgment received
5. Cancel-to-Send: cancel decision produced -> cancel sent
6. Cancel-to-Ack: cancel sent -> cancel acknowledgment received
7. Replace-to-Send: replace decision produced -> replace sent
8. Replace-to-Ack: replace sent -> replace acknowledgment received

## Primary KPI

The primary placement-speed metric is `tick_to_send_us`.

This is the key number for judging how fast the full system reacts and launches an order. `send_to_ack_us` is response latency from the broker/exchange path, not pure system placement speed.

## Timestamp Probes

Add timestamp probes at the major execution boundaries:

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

Use high-resolution monotonic timestamps for duration calculations.

## Sample Persistence

Persist every sample to:

```text
data/latency_baselines/YYYY-MM-DD/<run_id>.jsonl
```

Each record must include:

- `run_id`
- `timestamp_utc`
- `environment`
- `broker`
- `venue`
- `symbol`
- `strategy_id`
- `model_id`
- `trade_manager_id`
- `order_action`
- `side`
- `order_type`
- `quantity`
- `tick_to_decision_us`
- `decision_to_send_us`
- `tick_to_send_us`
- `send_to_ack_us`
- `cancel_to_send_us`
- `cancel_to_ack_us`
- `replace_to_send_us`
- `replace_to_ack_us`
- `success`
- `reject_reason`
- `raw_timestamps`

## Benchmark Command

Create a command similar to:

```powershell
python -m tools.latency_baseline.run `
  --env external `
  --broker rithmic `
  --symbol ES `
  --exchange CME `
  --duration 300 `
  --strategy latency_probe
```

## Reports

After each run, generate:

```text
reports/latency_baselines/<run_id>_summary.json
reports/latency_baselines/<run_id>_summary.md
```

The report should show min, mean, p50, p90, p95, p99, p99.9, max, and sample count for each latency metric.

Separate the report into three views:

- Offensive: Tick-to-Decision, Decision-to-Send, Tick-to-Send
- Defensive: Cancel-to-Send, Cancel-to-Ack, Replace-to-Send, Replace-to-Ack
- Round Trip: Send-to-Ack and total order/cancel/replace acknowledgment latency

## Baseline Comparison

Maintain a canonical baseline file:

```text
reports/latency_baselines/current_baseline.json
```

Each new run should compare against the current baseline and flag:

- improvement
- degradation
- unchanged
- percentage change
- absolute microsecond change
- p99 or p99.9 degradation

Default warning thresholds:

- p50 degradation greater than 10%
- p99 degradation greater than 15%
- p99.9 degradation greater than 20%
- hard fail if Tick-to-Send p99.9 degrades by more than 25%

## Synthetic Mode

Add a no-broker synthetic mode so the module can be tested without external connectivity:

```powershell
python -m tools.latency_baseline.run --mode synthetic --duration 30
```

Synthetic mode should verify:

- timestamp probes work
- durations are valid
- JSONL output is valid
- summary reports are generated
- baseline comparison works

## Documentation

Add:

```text
docs/LATENCY_BASELINE.md
```

Document:

- why placement speed and round-trip latency are different
- definitions of each metric
- where timestamps are captured
- how to run the benchmark
- how to read the report
- how to compare against the baseline
- how to update the canonical baseline

## Acceptance Criteria

The task is complete when:

- Tick-to-Send is measured separately from Send-to-Ack.
- Every latency sample is persisted.
- Summary reports are generated.
- New runs compare against the canonical baseline.
- Synthetic mode works without broker access.
- Documentation is included.
- The module can be reused as a standing repo baseline before future latency or execution changes.

## Non-Goals

- Do not optimize latency yet.
- Do not redesign the trading system.
- Do not change strategy logic.
- Do not require live-money trading.
- Do not treat acknowledgment latency as placement speed.
