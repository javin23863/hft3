# LATENCY.md — No-Fixed-Latency Policy and Budget

Version: 2026-06-18.

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

Moved with the crypto lane to the `hft3-crypto-lane` repo (split tag
`pre-lane-split-20260612`). Historical contract for archaeology:
`CRYPTO_LATENCY_BANDS_MS = [5.0, 50.0, 200.0]`, promotion-grade resolution
required `runtime/crypto_latency/latency_summary.json` with
`paper_order_latency.measured=true`.

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
Live offensive capability is engine 15.3 µs + live-endpoint wire. Wire survey
(CC-1 follow-up, 2026-06-11): trial SDK ships NO "Rithmic 01" live connection
params (distributed by Rithmic ops only, post-conformance); nearest measured
Rithmic-operated paper-infra node `ritpz01004.01.theomne.net` is 1.02 ms TCP
p50 from CHI404; live wire bound stays **OPEN** with labeled inference ~1–4 ms
from paper topology. Closing it requires: funded Rithmic 01 account + exchange
data agreements + API conformance (rapi@rithmic.com) + the resulting
connection_params.txt, then the existing probe with live guards.

**Probe-target defect (fixed in truth artifact, gate code fix pending)**: the
§5 `network_pass=false` verdict came from pinging `rituz00100.00.rithmic.com`
(37.5 ms — US East host NOT in the order path). Real endpoint RTT is 3.7 ms
p50. The network gate must re-point at the discovered live endpoint before the
verdict is meaningful.

**Defensive capacity note**: engine ceiling ~67k events/s; CME burst rates can
exceed this during shocks — queue-depth instrumentation and headroom check is
CC4 scope (CONTINUOUS_CME).

### 5.2 Live Rithmic 01 / Chicago Area placement (2026-06-18)

Artifact: `reports/latency_baselines/live_r01_chicago_baseline.json`
(run_id: `live_latency_test_v2_20260618T075012Z`, n=25 paired new+cancel on
CHI404, account 40262422, MESU6 far-from-market @7000, MD-primed per order).

**Do not conflate these clocks with §4 replay injection (ms ack).**

| View | Metric | p50 | p99 | Use in backtest |
|------|--------|-----|-----|-----------------|
| Offensive placement | `tick_to_send_us` | **27.3 µs** | **60.9 µs** | Min spacing between offensive fires; tactic feasibility |
| Offensive trigger | `tick_to_send_trigger_us` | 1.0 µs | 5.2 µs | Internal trigger-only bound (SDK entry, not return) |
| Defensive fire | `cancel_to_send_us` | **13.1 µs** | **18.9 µs** | Min time to fire cancel after decision |
| Defensive confirm | `cancel_to_ack_us` | — | **UNMEASURED** | Pending-state risk until measured |
| Round-trip ack | `send_to_ack_us` | 2.74 ms | 13.69 ms | Placement-test ack only (n=25) |
| Replay injection | `new_send_to_ack_ms` distribution | 3.54 ms | **9.81 ms** | `constant_order_latency` entry+response for HftBacktest (n=200 campaign) |

Capability report: `runtime/latency_reports/live_placement_capability.json`.

**Backtest execution budgets (conservative p99):**

- Offensive: assume **≥61 µs** from MD tick to SDK return before next new order.
- Defensive cancel fire: assume **≥19 µs** from cancel decision to cancel send.
- Replay order ack: inject **9.811 ms** (`live_order_latency.authoritative` in
  `runtime/latency_reports/latency_summary.json`).
- Cancel ack: **not yet injectable** — treat pending cancel as open until
  `cancel_to_ack_us` is measured.

Paper baseline comparison (`current_baseline.json`): paper `tick_to_send_us` p99
23.3 µs vs live 60.9 µs (+161%); paper `cancel_to_send_us` sample 14.7 µs vs
live 18.9 µs (+29%). Live is slower on placement but within the same
microsecond-loop band (`<100 µs`).

### 5.3 HftBacktest three-component latency and regimes

Authority: [docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md](../docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md).

**Naming:** `runtime/latency_reports/latency_summary.json` uses `new_send_to_ack_ms`
as a distribution object (`us` + `ms` blocks). Legacy `live_order_ack_p99_ms` is
deprecated (still read for backward compatibility).

**Injection:** separate `order_entry_latency_ms` and `order_response_latency_ms`
when `latency_model.json` is provided; otherwise symmetric split of measured ack.

**Regime artifacts:** `reports/latency_baselines/live_r01_chicago/latency_model_{fast,normal,stress,extreme}.json`

Generate: `python scripts/latency_probe/generate_latency_regimes.py`

**Do not** combine unrelated quantiles across components. Normal regime should
use `IntpOrderLatency` samples from `<run_id>_intp_samples.jsonl` when CC-3
campaign completes.

**Campaign orchestrator:** `scripts/chi404_run_latency_component_campaign.sh`

---

## 6. Feature Clock

Source: active workbench/HftBacktest latency configuration.

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

Moved with the crypto lane to the `hft3-crypto-lane` repo (split tag `pre-lane-split-20260612`).
