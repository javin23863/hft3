# LATENCY.md — No-Fixed-Latency Policy and Budget

Version: 2026-06-11.

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

Default `latency_ms=50.0` for all four exchange builders
(`build_binance_hftbacktest`, `build_kraken_hftbacktest`,
`build_bitfinex_hftbacktest`, `build_coinbase_hftbacktest`).

**Contract** (resolves the former TODO):

```python
CRYPTO_LATENCY_BANDS_MS = [5.0, 50.0, 200.0]
```

To be defined as a named constant in `crypto_hft_builder.py`
(ALPHA_CRYPTO.md campaign deliverable); until the constant lands, `[5, 50,
200]` ms is the binding sweep list for any crypto sweep runner.

**Crypto latency resolution** (analog of §4, promotion-grade replay):

1. CLI `--latency-ms` if provided.
2. `runtime/crypto_latency/latency_summary.json` with
   `paper_order_latency.measured=true` AND `order_ack_p99_ms` present
   (see §10).
3. Neither → raise `ValueError` (UNMEASURED). TCP connect-time and WS
   ping/pong RTT are never silent fallbacks for order-ack latency.

**Venue RTT source labels** (`venue_profiles.json`, written by
`packages/crypto_lane/src/align/latency_profile.py`): only
`live_measured:<venue>` and `ws_rtt:*` sources are promotion-eligible;
`synthetic_calibrated:*` profiles are research-only and must hard-fail the
promotion path (CRYPTO_LIVE.md §8 row K7).

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
(run_id: `20260611T074546Z`).

| Metric | Value | Status |
|--------|-------|--------|
| cyclictest p99 (loaded) | 11 µs | PASS (limit 20 µs) |
| Rithmic TCP p99 (port 65000) | 4.09 ms | recorded |
| Order-ack p99 | 6.256 ms | MEASURED — authoritative |
| Order-ack p50 | 3.483 ms | |
| Order-ack p90 | 3.753 ms | |
| Order-ack p99.9 | 13.768 ms | |
| `paper_order_latency.measured` | true (as of 2026-06-11) | |
| `trial_order_ack_appendix.authoritative` | superseded — see §9.3 | |

Campaign: `order_ack_campaign_20260611T072116Z`. Venue: Rithmic paper / Chicago.
Symbol: MESM6. Samples: 1002 paired submit→ack, reject=0. Measurement tool:
`rithmic_latency_probe` (native C++; `measurement_tier: native_cpp_probe`,
`hot_path_language: c++`, `wrapper: none`). Orchestrator:
`scripts/chi404_run_paper_latency_sweep.sh`.

`rithmic_app_latency.status = "OK"` — order-ack p99 measured via native probe.

`network_pass = false` — rithmic_tcp_65000 p99 4094 µs exceeds 500 µs network
limit used by lane_1 criteria.

**Resolver rung 2** (`paper_order_latency.measured=true`, `order_ack_p99_ms`
present) now returns **6.256 ms** as the authoritative latency for replay runs.
Value is within the [0.5, 10] ms validation band accepted by
`validate_replay_latency_ms()`.

**Honest floor**: CME lane order-ack measured at p99 6.256 ms via retail
Rithmic paper broker from CHI404 (no kernel bypass, no co-location fiber).
TCP connect-time (4.09 ms p99) is a network health metric only — it is not
used as an execution latency proxy anywhere in the pipeline.

### 5.1 Component decomposition (CC-1 Latency Truth, 2026-06-11)

Artifact: `runtime/latency_reports/latency_truth.json` (CHI404 campaign).
Four clocks, never conflated:

| Component | Measured | Tool |
|---|---|---|
| `evaluate_actions` | p50 **40 ns** (1M iters) | `rithmic_gateway/tools/bench_decision.cpp` |
| SPSC push+pop round-trip | p50 **20 ns** (1M iters) | `rithmic_gateway/tools/bench_spsc.cpp` |
| Full engine loop tick→decision | **15.3 µs/event** (~65–67k events/s sustained; within the 18 µs §7 budget) | `hft3_engine` REPLAY, 500k events, core-pinned |
| Kernel jitter (loaded) | cyclictest p99 10 µs | run `20260611T074546Z` |
| Wire to CHI404 upstream gateway | ping p50 90 µs / p99 180 µs | same run |
| Wire to real paper order endpoint `ritpz04031.04.theomne.net` (38.98.144.227) | TCP RTT p50 3.69 ms / p99 4.14 ms | CC-1 `ss -tnp` endpoint discovery + 30-sample probe |
| Paper order ack (MESU6 fresh n=200) | p50 4.19 ms / p99 13.69 ms | `rithmic_latency_probe` |

**Key reading**: paper ack p50 (3.5–4.2 ms) ≈ paper-endpoint TCP RTT p50
(3.7 ms) — the measured ack latency is dominated by **distance/handling to
Rithmic's paper cluster**, not engine or simulator compute. The paper p99
remains the conservative injection value for research (§4 rung 2 unchanged).
Live offensive capability is engine 15.3 µs + live-endpoint wire — bounded
below by ~100–250 µs IF live order infrastructure is Aurora-local; this stays
`live_unknown` until a live session measures it (CONTINUOUS_CME CC-1 follow-up).

**Probe-target defect (fixed in truth artifact, gate code fix pending)**: the
§5 `network_pass=false` verdict came from pinging `rituz00100.00.rithmic.com`
(37.5 ms — US East host NOT in the order path). Real endpoint RTT is 3.7 ms
p50. The network gate must re-point at the discovered live endpoint before the
verdict is meaningful.

**Defensive capacity note**: engine ceiling ~67k events/s; CME burst rates can
exceed this during shocks — queue-depth instrumentation and headroom check is
CC4 scope (CONTINUOUS_CME).

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

---

## 9. Order-Ack Measurement Campaign

### 9.1 Requirement

`paper_order_latency.measured` in `runtime/latency_reports/latency_summary.json`
must flip to `true` before the automated resolver (§4 rung 2) can supply
`order_ack_p99_ms` to replay runs and promotion bundle construction
(DEPLOYMENT.md §1.3 `latency_ms_at_promotion`). Until that flag is set, every
replay and promotion bundle construction requires an explicit `--latency-ms`
argument.

### 9.2 Timestamp Protocol

**Real monotonic timestamps only.** The submit and ack timestamps recorded per
order must come from `time.perf_counter_ns()` (or `std::chrono::steady_clock`
on the C++ side) at the actual callback boundaries:

- **submit_ns**: captured immediately before `send_prepared_limit_order()` is
  called on the wire path.
- **ack_ns**: captured inside the Rithmic order-event callback at the moment
  the callback is entered (before any processing).

The synthetic waterfall probe in `PaperLatencyDaemon._shadow_probe_mono_ns()`
(lines ~53–57: `t1 = t0 + 1000; t2 = t1 + 500; t3 = t2 + 500`) must **not**
be used to populate submit/ack timestamps in paired records. Records sourced
from that probe must carry `shadow_synthetic: true` and must never appear in
the authoritative `paper_order_latency` section of `latency_summary.json`.
See CORRECTNESS.md §2 row 10 and §3 defect (b).

### 9.3 Sample Size Gate

Minimum **n ≥ 1000** paired submit→ack samples required before the campaign
may set `paper_order_latency.measured = true`. Samples must be collected from
actual Rithmic paper-broker sessions on CHI404 (not simulated). The resolver
additionally requires `order_ack_p99_ms` to be present in the summary
(§4 rung 2).

The n ≥ 1000 requirement is now satisfied. Campaign
`order_ack_campaign_20260611T072116Z` collected 1002 paired submit→ack samples
(reject=0) via `rithmic_latency_probe` (native C++) on Rithmic paper / Chicago,
symbol MESM6, run 2026-06-11. `paper_order_latency.measured` was set to `true`
in `runtime/latency_reports/latency_summary.json` (run_id `20260611T074546Z`).

The prior `trial_order_ack_appendix` (n=12, run_id `20260530T031754Z`) is
superseded by the native-probe campaign. That appendix is retained as
non-authoritative fallback history only; the `rtrader` log-bridge tier from
which it was sourced remains a non-authoritative fallback and must not be used
for promotion-grade latency resolution.

### 9.4 Campaign Unblock

Setting `paper_order_latency.measured = true` (with valid `order_ack_p99_ms`)
unblocks:

1. §4 resolver rung 2 — automated latency injection for replay runs.
2. ALPHA_CME.md M5 gate — research sweep at measured p99 (M6) may begin.
3. DEPLOYMENT.md §1.3 — `latency_ms_at_promotion` may be drawn from the
   measured value rather than requiring an explicit CLI override.

Until this campaign completes, every replay invocation and every promotion
bundle construction requires `--latency-ms` explicitly (§4 rung 1).

---

## 10. Crypto Order-Ack Measurement Campaign

Crypto analog of §9. Lane-scoped artifact:
`runtime/crypto_latency/latency_summary.json` (separate from the CME
`runtime/latency_reports/latency_summary.json`).

### 10.1 Requirement

`paper_order_latency.measured` in `runtime/crypto_latency/latency_summary.json`
must flip to `true` before the crypto resolution rung 2 (§3) can supply
`order_ack_p99_ms` to replay runs and crypto bundle construction
(CRYPTO_LIVE.md §7 `latency_ms_at_promotion`). Until then, every crypto
replay and bundle build requires an explicit `--latency-ms` argument.

### 10.2 Timestamp Protocol

§9.2 applies verbatim with venue substitutions:

- **submit_ns**: `time.perf_counter_ns()` captured immediately before the
  Bitfinex order-new wire call in the crypto adapter.
- **ack_ns**: captured on entry of the venue order-ack WebSocket callback,
  before any processing.

No synthetic or derived timestamps in paired records; synthetic records must
carry `shadow_synthetic: true` and never populate the authoritative
`paper_order_latency` section.

### 10.3 Sample Size Gate

Minimum **n ≥ 1000** paired submit→ack samples, collected from Bitfinex paper
sub-account sessions running on the crypto live host (Contabo VPS,
CRYPTO_LIVE.md §2) — not from the workstation, and not simulated.

### 10.4 Campaign Unblock

Setting `paper_order_latency.measured = true` (with valid `order_ack_p99_ms`)
unblocks:

1. §3 crypto resolution rung 2 — automated latency injection for crypto
   replay runs.
2. ALPHA_CRYPTO.md C9 gate — the C10 sweep at measured p99 may begin.
3. CRYPTO_LIVE.md §7 — `latency_ms_at_promotion` may be drawn from the
   measured value rather than requiring an explicit CLI override.
