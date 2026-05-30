# Workbench latency architecture

Python is allowed for **research, orchestration, visualization, parameter sweeps, and dashboarding**.

Python runtime must **not** be the source of truth for production latency when the live hot path is C++.

## Three approved approaches

| Priority | Approach | Status in hft3 |
|----------|----------|----------------|
| 1 Preferred | C++ hot path via pybind; Python backtest calls same code | `decision_engine/cpp/` — binding TBD |
| 2 Acceptable | Python model + **measured C++ latency injection** | **Implemented:** `workbench/src/sim/cpp_latency_profile.py`, `latency_injector.py` |
| 3 Best replay | Historical MBO through C++ production engine | **Stub:** `workbench/src/sim/cpp_replay_harness.py` |

## Required per-decision fields

| Field | Source |
|-------|--------|
| `market_data_exchange_ts` | MBO event time |
| `market_data_receive_ts` | exchange + feed_delay (C++ measured) |
| `decision_start_ts` / `decision_end_ts` | receive + cpp_decision_compute |
| `order_send_ts` | decision_end + decision_to_send |
| `gateway_ack_ts` | order_send + send_to_ack (gateway) |
| `fill_ts` | backtest fill |

Derived (microseconds): `feed_delay_us`, `decision_compute_us`, `decision_to_send_us`, `send_to_ack_us`, `tick_to_ack_us`, `tick_to_fill_us`.

`python_research_compute_us` is logged but **informational only**.

## Measured C++ distributions

Loaded from CHI404 `runtime/latency_reports/latency_summary.json` + `workbench/config/cpp_latency_profile.yaml`:

- `cpp_decision_compute_p50/p95/p99_us` — cyclictest loaded (until dedicated C++ probe)
- `order_send_p50/p95/p99_us` — Rithmic TCP proxy
- `gateway_ack_p50/p95/p99_us` — TCP proxy until R|API+ wired

## Injection sweep (µs)

0, 50, 100, 250, 500, 1k, 2k, 5k, 10k, 25k, 50k, 100k, 250k, 1s

## Viability rule

**Promote Candidate** requires:

- `simulated_latency_adjusted_pnl > 0` at measured production p99
- `survives_cpp_execution_delay == true`
- positive latency profitability buffer vs C++ p99
- robustness pack pass

Never promote on raw Python backtest PnL alone.
