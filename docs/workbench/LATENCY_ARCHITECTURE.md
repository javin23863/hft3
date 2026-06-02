# Workbench latency architecture

Python is allowed for **research, orchestration, visualization, parameter sweeps, and dashboarding**.

Python runtime must **not** be the source of truth for production latency when the live hot path is C++.

## Three approved approaches

| Priority | Approach | Status in hft3 |
|----------|----------|----------------|
| 1 Preferred | C++ hot path via pybind; Python backtest calls same code | `decision_engine/cpp/` — binding TBD |
| 2 Acceptable | Python model + **measured C++ latency injection** | **Implemented:** `workbench/src/sim/cpp_latency_profile.py`, `latency_injector.py` |
| 3 Best replay | Historical MBO through C++ production engine | **Stack self-test in CI**; NPZ replay **not implemented** |

## Required Per-Decision Fields

Phase 5 workbench runs emit 33 nanosecond timestamp columns per trade in `trades.parquet`. `diagnostics.json.phase5_timestamp_schema` records the schema version, required count, trade count, completeness, and monotonicity status.

| Field | Source |
|-------|--------|
| `market_data_exchange_ts` | MBO event time |
| `market_data_wire_ts` / `market_data_receive_start_ts` | exchange plus measured feed-delay submarkers |
| `market_data_receive_ts` | exchange + feed_delay (C++ measured) |
| `market_data_decode_start_ts` / `market_data_decode_end_ts` | receive-time decode markers |
| `book_snapshot_start_ts` / `book_snapshot_end_ts` | book-state materialization markers |
| `feature_build_start_ts` / `feature_build_end_ts` | feature construction markers |
| `signal_start_ts` / `signal_end_ts` | signal evaluation markers |
| `decision_start_ts` / `decision_end_ts` | receive + cpp_decision_compute |
| `risk_check_start_ts` / `risk_check_end_ts` | risk-check markers |
| `sizing_start_ts` / `sizing_end_ts` | sizing markers |
| `order_intent_create_ts` | order-intent creation marker |
| `order_queue_enter_ts` / `order_queue_exit_ts` | order queue markers |
| `order_send_ts` | decision_end + decision_to_send |
| `gateway_send_ts` | gateway submit marker |
| `gateway_ack_ts` | order_send + send_to_ack (gateway) |
| `exchange_ack_ts` / `queue_position_ts` | exchange acknowledgement and queue-position markers |
| `fill_model_start_ts` / `fill_model_end_ts` | fill-model markers |
| `fill_ts` | backtest fill |
| `pnl_mark_start_ts` / `pnl_mark_end_ts` | PnL marking markers |
| `audit_record_start_ts` / `audit_record_end_ts` | audit persistence markers |

Derived (microseconds): `feed_delay_us`, `decision_compute_us`, `decision_to_send_us`, `send_to_ack_us`, `tick_to_ack_us`, `tick_to_fill_us`.

`python_research_compute_us` is logged but **informational only**.

## Measured C++ distributions

Loaded from CHI404 `runtime/latency_reports/latency_summary.json` + `workbench/config/cpp_latency_profile.yaml`:

- `cpp_decision_compute_p50/p95/p99_us` — cyclictest loaded (until dedicated C++ probe)
- `gateway_ack_p50/p95/p99_us` — **measured R|Trader paper submit→ack** when `order_ack_measured=true` (≥1,000 paired orders)
- `order_send_*` — zero when ack is folded into `gateway_ack`; blocked until paper measurement
- `network.rithmic_tcp_65000` — **network health only**; never used for `gateway_ack` or replay default latency

Paper waterfall: `runtime/paper_latency/raw/<run_id>/records.ndjson` → `latency_waterfall.json` via `scripts/latency_probe/build_waterfall_report.py`. Daemon: `python -m data_system.rithmic_trial.pipeline paper-latency-daemon` (CHI404 only).

Until `order_ack_measured=true`, replay scripts require explicit `--latency-ms`; TCP connect is not a silent fallback.

## Injection sweep (µs)

0, 50, 100, 250, 500, 1k, 2k, 5k, 10k, 25k, 50k, 100k, 250k, 1s

## Viability rule

**Promote Candidate** requires:

- `simulated_latency_adjusted_pnl > 0` at measured production p99
- `survives_cpp_execution_delay == true`
- positive latency profitability buffer vs C++ p99
- robustness pack pass

Never promote on raw Python backtest PnL alone.

## Hot-path audit

See [HOT_PATH_AUDIT.md](HOT_PATH_AUDIT.md) for CMake targets, topology, and remaining R\|API+ gaps.
