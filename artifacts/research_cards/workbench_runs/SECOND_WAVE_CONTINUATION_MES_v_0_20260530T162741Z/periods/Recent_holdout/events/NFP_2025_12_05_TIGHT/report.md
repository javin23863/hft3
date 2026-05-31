# Workbench Run Report: SECOND_WAVE_CONTINUATION

- **Event / period:** NFP_2025_12_05_TIGHT (NFP_2025_12_05_TIGHT)
- **Latency authority:** C++ measured (Python runtime informational only)

## Runtime (do not conflate)

- **Python research runtime:** 52539572.0 µs (informational only)
- **C++ hot-path runtime (p99):** 4105.2 µs (source of truth)

## Latency viability

- **Measured production p99:** 4105.2 µs (4.1052 ms)
- **Break-even latency:** 2000000.0 µs (2000.0000 ms)
- **Latency profitability buffer:** 1995894.8 µs
- **Simulated latency-adjusted PnL:** $-3.67
- **Survives C++ execution delay:** False
- **Lane required / measured:** sub_10ms / sub_10ms
- **Recommendation:** MARGINAL

## C++ latency profile (µs)

- `cpp_decision_compute_p50_us`: 5.5
- `cpp_decision_compute_p95_us`: 9.9
- `cpp_decision_compute_p99_us`: 11.0
- `order_send_p50_us`: 0.0
- `order_send_p95_us`: 0.0
- `order_send_p99_us`: 0.0
- `gateway_ack_p50_us`: 0.0
- `gateway_ack_p95_us`: 0.0
- `gateway_ack_p99_us`: 0.0
- `measured_production_p99_us`: 4105.202995474916

## Robustness

Pack passed: **True**
Over-fit risk: **low**

**Viability rule:** A strategy is viable only if it remains profitable after measured C++ hot-path latency, gateway latency, fill assumptions, slippage, fees, and adverse selection — not because Python backtest PnL is positive.
