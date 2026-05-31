# Event replay: CPI_2024_09_11_TIGHT

- **release_date:** 2024-09-11
- **window UTC:** 2024-09-11T12:29:30+00:00 → 2024-09-11T12:35:00+00:00
- **NPZ:** `data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz` (146184 events)
- **latency:** 4.0942 ms (CHI404 rithmic_tcp_65000 p99 (measured on colo bare metal))
- **CHI404 probe:** 20260530T031754Z
- **primary research engine:** event_accurate_mbo

## CHI404 measured speed

| Metric | Value |
|--------|-------|
| CPU loaded p99 | 11 µs |
| Gateway ping p99 | 0.166 ms |
| Rithmic TCP p99 | 4.0942 ms |
| Order ack p99 | not measured |

## Engine 1: hftbacktest_loop (queue-realistic)

MBO features synced to `hbt.current_timestamp`; max-abs aggregation; threshold 0.15.

- steps: None
- balance: None
- num_trades: None
- position: None

## Engine 2: event_accurate_mbo (research path)

SignalBacktester with full MarketStatePipeline; per-hypothesis evaluation.

- hypotheses with trades: 9 / 39
- total trades (all hyps): 3506
- SPREAD_BLOWOUT_RECOMPRESSION trades: 11
- SPREAD_BLOWOUT_RECOMPRESSION net PnL: $-194.275

## Limits

- Zero trades on the old depth-only mean@0.25 path was a wiring issue, not missing edge.
- hftbacktest_loop and event_accurate_mbo measure different fill models; compare explicitly.
- Replay body is Databento MBO for the macro event window, not Rithmic historical tape.
