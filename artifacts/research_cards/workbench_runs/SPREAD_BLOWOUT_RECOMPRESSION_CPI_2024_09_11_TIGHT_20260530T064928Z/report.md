# Workbench Run Report: SPREAD_BLOWOUT_RECOMPRESSION

- **Event / period:** CPI_2024_09_11_TIGHT (CPI_2024_09_11_TIGHT)
- **Latency authority:** C++ measured (Python runtime informational only)

## Runtime (do not conflate)

- **Python research runtime:** 86584210.8 µs (informational only)
- **C++ hot-path runtime (p99):** 12293.6 µs (source of truth)

## Latency viability

- **Measured production p99:** 12293.6 µs (12.2936 ms)
- **Break-even latency:** 2000000.0 µs (2000.0000 ms)
- **Latency profitability buffer:** 1987706.4 µs
- **Simulated latency-adjusted PnL:** $-135.22
- **Survives C++ execution delay:** False
- **Lane required / measured:** sub_10ms / 10_250ms
- **Recommendation:** MARGINAL

## C++ latency profile (µs)

- `cpp_decision_compute_p50_us`: 5.5
- `cpp_decision_compute_p95_us`: 9.9
- `cpp_decision_compute_p99_us`: 11.0
- `order_send_p50_us`: 3611.47899820935
- `order_send_p95_us`: 4071.4510032557882
- `order_send_p99_us`: 4094.202995474916
- `gateway_ack_p50_us`: 3611.47899820935
- `gateway_ack_p95_us`: 4071.4510032557882
- `gateway_ack_p99_us`: 4094.202995474916
- `measured_production_p99_us`: 12293.608986424748

## Robustness

Pack passed: **True**
Over-fit risk: **low**

**Viability rule:** A strategy is viable only if it remains profitable after measured C++ hot-path latency, gateway latency, fill assumptions, slippage, fees, and adverse selection — not because Python backtest PnL is positive.
