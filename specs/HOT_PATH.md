# HOT_PATH.md — Research and Live Hot-Path Architecture

Version: 2026-06-10.

---

## 1. Research Path: Current Costs and Benchmark Anchor

Python replay via hftbacktest + numba JIT. Key cost sources per step:

| Cost item | Location | Notes |
|-----------|----------|-------|
| `hbt.elapse(step_ns)` boxing | numba jitclass boundary | Called every 100 µs step regardless of open orders |
| `adapter.after_elapse(ts)` | `packages/execution/adapters/hftbacktest_simulated_exchange.py` | Fast-path landed: early-exit when `_open_orders` is empty avoids `hbt.state_values()` + `hbt.orders()` numba calls per step |
| `hbt.orders(0)` + `hbt.state_values(0)` | numba | Only called when `_open_orders` non-empty (after fast-path) |
| 4× `heapq.nlargest/nsmallest` per event | `mbo_features.py OrderBook.top_k_depth()` | Called for K=1,3,5,10; 4 separate heapq scans per event |
| `np.median(deque(100))` per event | `mbo_features.py _extract_features()` | Full O(N) scan + sort for SPREAD_STRESS every tick |
| `np.std(deque(100))` per event | `mbo_features.py _extract_features()` | Full O(N) for REALIZED_VOL_STATE every tick |
| `vector_to_feature_dict(vec)` per step | `market_state_pipeline.py line 80` | Dict allocation on every step (used by regime filter and hypothesis `state.f()`) |

Benchmark anchor: TODO — no verified wall-clock timing for 113s/session
(146k events, 3.3M steps, 1 hypothesis) was found in code or artifacts at
time of writing. These numbers are cited in the system brief as the optimization
target baseline but are not embedded in any file in the repository. Flag this
as unverified until a benchmark run confirms it.

Target: ≤10s for the same workload.

---

## 2. Research Path: Ranked Optimization Roadmap

Listed by expected impact (highest first):

### 2.1 Event-driven stepping (highest impact)
Replace fixed `step_ns=100_000` (100 µs) polling loop with event-driven
advancement. Current: hbt steps at a fixed rate even when no MBO events
arrive. Switching to hbt's event-driven tick mode eliminates the majority of
no-op `hbt.elapse()` calls.

### 2.2 Incremental top-K (replace 4× heapq scans)
Location: `packages/features_engine/src/features/mbo_features.py`
`OrderBook.top_k_depth()`.

Current: 4 independent `heapq.nlargest/nsmallest(k, dict.keys())` calls per
event (K=1,3,5,10). Each scans the full bid/ask dict.

Fix: maintain a sorted structure (e.g. `sortedcontainers.SortedList`) or a
single pass that returns all K-depths simultaneously. This removes O(N log K)
× 4 per-event cost.

### 2.3 O(1) running stats (replace per-event np.median/np.std over deque(100))
Location: `packages/features_engine/src/features/mbo_features.py`
`_extract_features()`.

Current:
- `np.median(self._spread_history)` — O(N) sort over deque(100) per event.
- `np.std(self._mid_returns)` — O(N) pass over deque(100) per event.

Fix: maintain running sorted structure for median (e.g. two-heap or SortedList
with O(log N) update) and running Welford accumulator for std (O(1) update).

### 2.4 Drop vector_to_feature_dict from hot loop
Location: `packages/features_engine/src/pipeline/market_state_pipeline.py`
line 80.

Current: `feat_dict = vector_to_feature_dict(vec)` allocates a new dict on
every step and iterates all 36 named slots. The regime filter and hypothesis
`state.f()` both fall back to this dict when the feature vector index lookup
fails.

Fix: pass `feature_vector` directly through the hot path; patch `RegimeFilter`
to accept a numpy array directly; eliminate the dict allocation for the
hypothesis-evaluation hot loop.

### 2.5 pybind C++ extractor (stretch)
Location: `packages/decision_engine/cpp/` (`hft_features` CMake target).
`LATENCY_ARCHITECTURE.md` classifies this as Approach 1 (preferred) but
"binding TBD". Achieves the feature → decision portion of the live budget.

---

## 3. Live Path Architecture

### 3.1 SPSC Queues

Source: `rithmic_gateway/include/spsc_queue.hpp`.

`SPSCQueue<T, 8192>` — capacity 8192 (power of 2 enforced by static_assert).
Cache-line aligned (`alignas(CACHE_LINE_SIZE)` where `CACHE_LINE_SIZE = 64`).
`head_` and `tail_` on separate cache lines (explicit padding).
Fully heap-allocation-free; `push/pop` are `noexcept`.

Two queues in `RithmicAdapter`:
- `SPSCQueue<MarketDataEvent, 8192>* mbo_queue_`
- `SPSCQueue<OrderEvent, 8192>* order_queue_`

### 3.2 C++ Feature Extractor

`std::array<double, 64>` embedded in `MarketState.model_features`
(64-byte aligned struct, `decision_runtime.hpp`).
CMake target: `hft_features`.

### 3.3 Decision Runtime

Source: `packages/decision_engine/cpp/src/decision_runtime.cpp`.
`DecisionEngine::evaluate_actions()` — dot product over
`min(active_feature_count_, 64)` weights; zero dynamic allocation.
Weights binary format: 16-byte header (magic `0x48465433` little-endian,
version, model_id, feature_count) + 1024 × double (zero-padded).
Python export: `walk_forward.export_weights_to_cpp()`.

### 3.4 Risk Engine

Source: `risk_engine/include/risk_manager.hpp`, `risk_engine/src/risk_manager.cpp`.
`RiskManager::check_order()` — atomics (`std::atomic<int32_t>` position,
`std::atomic<bool>` halted/flatten_active/hard_halt) + lock-free
`SlidingWindowCounter` (256-slot ring buffer, 1-second sliding window,
lock-free relaxed reads).

### 3.5 Rithmic Gateway Safety Flags

Source: `rithmic_gateway/include/rithmic_adapter.hpp`.

All flags are `std::atomic` — safe to read from the gateway consumer thread:

| Flag | Setter event | Required consumer action |
|------|-------------|--------------------------|
| `order_halt()` | order-event queue drop | halt trading immediately |
| `md_data_gap()` | MD queue overrun | force book resync or halt |
| `position_desync()` | trade bust ('B') | immediate reconciliation |
| `order_desync()` | not-modified ('N') | immediate reconciliation |
| `auto_liquidate_halt()` | broker force-flatten ('L') | halt and reconcile |
| `adm_alert_severity()` | AdmCallbacks alert ≥2 | operator attention or halt |

**REQUIRED before live**: the gateway consumer loop must poll all six flags on
every iteration and halt/reconcile on any non-zero value. Consumer is being
added in the current release (Phase 5a). Clear flags via corresponding
`clear_*()` methods after action is taken.

---

## 4. System Requirements for Live Hot Chain

1. CPU isolation and core map: dedicated cores for MBO consumer, feature
   extractor, decision engine, order submitter. Not yet configured on CHI404.
2. Busy-spin consumers: no `sched_yield` or sleep in hot loop.
3. No allocation post-init: all buffers pre-allocated; `SPSCQueue` uses
   stack/pre-allocated array; `DecisionEngine` weights loaded at startup.
4. Hugepages: where supported on CHI404 kernel; reduces TLB miss latency.

Items 1–4 are listed requirements; current CHI404 configuration status is
not verified in code artifacts at time of writing.
