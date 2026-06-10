# LATENCY.md — No-Fixed-Latency Policy and Budget

Version: 2026-06-10.

---

## 1. Policy: No Fixed Latency

No single latency value is hard-coded for promotion decisions. Research sweeps
the full band per lane; promotion requires positive expectancy at the measured
order-ack p99. Until order-ack is measured, an explicit `--latency-ms` CLI
argument is required; TCP connect-time is not a silent fallback.

---

## 2. CME Lane: Research Sweep Bands

Source: `packages/backtest_pipeline/src/hft_backtest_builder.py`

```python
LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]
```

Applied via `build_hftbacktest(data_path, latency_ms=..., queue_model_type=...)`
using `constant_order_latency` (hftbacktest 2.4+) or `constant_latency`
(hftbacktest 2.3 fallback). Queue models: `LogProbQueueModel2` (default),
`SquareProbQueueModel`.

---

## 3. Crypto Lane: Research Sweep Bands

Source: `packages/backtest_pipeline/src/crypto_hft_builder.py`

Default `latency_ms=50.0` for all four exchange builders. The system brief
specifies `[5, 50, 200]` ms sweep; the builder functions accept any value.

TODO: a matrix sweep list analogous to `LATENCY_BANDS_MS` for crypto is not
explicitly defined in `crypto_hft_builder.py`. Treat `[5, 50, 200]` ms as the
intended sweep but verify this against any crypto sweep runner script before
use.

---

## 4. CHI404 Latency Resolution Hierarchy

Source: `packages/backtest_pipeline/src/chi404_latency.py`.
Summary file: `runtime/latency_reports/latency_summary.json`.

`resolve_replay_latency_ms()` resolution order:
1. CLI `--latency-ms` if provided (validated against band [0.5, 10.0] ms by
   `validate_replay_latency_ms()`).
2. `paper_order_latency.measured=true` AND `order_ack_p99_ms` in summary →
   source label `paper_order_latency.authoritative`.
3. `trial_order_ack_appendix.status=ok` AND `authoritative=true` AND
   count >= 1000 paired samples → source label
   `trial_order_ack_appendix.authoritative`.
4. Neither measured → raises `ValueError` (UNMEASURED note).

`BACKTEST_LATENCY_NOTE_UNMEASURED` is the current default alias
(`BACKTEST_LATENCY_NOTE`).

---

## 5. Current CHI404 Measured State

Source: `runtime/latency_reports/latency_summary.json`
(run_id: `20260530T031754Z`).

| Metric | Value | Status |
|--------|-------|--------|
| cyclictest p99 (loaded) | 11 µs | PASS (limit 20 µs) |
| Rithmic TCP p99 (port 65000) | 4.09 ms | recorded |
| Order-ack p99 | UNMEASURED | R\|API+ not wired |
| `paper_order_latency.measured` | false | |
| `trial_order_ack_appendix.authoritative` | false (n=12, need ≥1000) | |

`rithmic_app_latency.status = "BLOCKED"` —
reason: "R|API+ not wired; order ack p99 unavailable".

`network_pass = false` — rithmic_tcp_65000 p99 4094 µs exceeds 500 µs network
limit used by lane_1 criteria.

**Honest floor**: Today's realistic CME lane order-ack is 2–10 ms via retail
Rithmic (no kernel bypass, no co-location fiber). The CHI404 colo hardware
passes cyclictest but order-ack is unmeasured because R|API+ is not yet wired.
TCP connect-time (4.09 ms p99) is a network health metric only — it is not
used as an execution latency proxy anywhere in the pipeline.

---

## 6. Feature Clock

Source: `packages/replay/replay_session.py` `ReplaySessionConfig.feature_latency_ms`.

Default: `None` → mirrors `latency_ms`.
Effect: feature clock shifted back by `feat_latency_ns = feat_latency_ms * 1e6`
so the strategy observes features as stale as the configured feed latency.
Set to 0.0 for perfectly fresh features (no feed delay modelling).

---

## 7. Live Budget Table (µs targets)

| Stage | Target | Implementation |
|-------|--------|---------------|
| MD callback → book | ≤5 µs | C++ `RithmicAdapter` → `SPSCQueue<MarketDataEvent, 8192>` |
| Book → features | ≤10 µs | C++ feature extractor (`hft_features` target) |
| Features → decision | ≤2 µs | `DecisionEngine::evaluate_actions()` dot product, `std::array<double, 64>` |
| Risk check | ≤1 µs | `RiskManager::check_order()` — atomics + sliding-window counter |
| Submit → wire | Rithmic-bound (ms) | `RithmicAdapter::send_prepared_limit_order()` |

The decision engine dot product iterates `min(active_feature_count_, 64)`
weights with no heap allocation on the hot path
(`packages/decision_engine/cpp/src/decision_runtime.cpp`).

The risk engine uses `std::atomic<int32_t>` position and `SlidingWindowCounter`
(256-slot ring buffer, lock-free) for rate limiting
(`risk_engine/include/risk_manager.hpp`).

---

## 8. Promotion Gate

A hypothesis is promotion-eligible only when:
- `net_expectancy > 0` at the measured order-ack p99 (or, until measured, at
  the explicit `--latency-ms` value).
- Walk-forward OOS kill-gate passes on all four periods.
- Backtester certification stamp is GREEN and not stale.

Until `order_ack_measured=true` in `runtime/latency_reports/latency_summary.json`,
no CME lane hypothesis can be promoted to live using the automated resolver;
explicit `--latency-ms` is required for every replay run.
