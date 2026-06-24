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

Benchmark: `python scripts/bench_replay_session.py` on
`MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz` (146,184 events, 330 s window,
1 hypothesis, latency 1.0 ms). Measured progression on the dev box:

| State | Wall clock | Steps |
|-------|-----------|-------|
| Pre-fix (after_elapse boxing every step) | ~5,400 s (extrapolated; killed) | 3.3 M |
| + open-order fast path | 113 s | 3.3 M |
| + incremental top-K, running stats, lazy MarketState | 33.7 s | 3.3 M |
| + event-driven stepping (`step_mode="event"`, default) | 25.9 s | 117.6 k |
| + ns-interval-cached `resolve_ns`, pure-python softmax | **24.4 s** | 117.6 k |

Grid mode (`step_mode="grid"`, ablation/legacy) after the same feature-path
work: 78.3 s. Remaining cost is split between the python feature extractor's
per-event book/feature math and residual numba boxing on `depth()`/stepping
calls; the ≤10 s target needs the stretch item (§2.5 pybind C++ extractor).

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

**REQUIREMENT (still stands)**: the gateway consumer loop MUST poll all six flags on
every iteration and halt/reconcile on any non-zero value. Clear flags via corresponding
`clear_*()` methods after action is taken.

**Contract location moved**: the normative contract for the six-flag poll — execution
order, SafetyPoller.poll() ordering, failure-action table, and ack/clear protocol — is
**CHI404_RUNTIME.md §3 and §5**. This section (§3.5) is **informative only**; it
describes the flags and their setter events. CHI404_RUNTIME.md §3/§5 is the authoritative
source. This section is not superseded — the REQUIREMENT above still holds — only the
contract location has moved.

### 3.6 pybind11 Research Binding

A pybind11 module `hft3_features_cpp` exposes the C++ `FeatureExtractor` to
Python research tooling through the root CMake target `hft3_features_cpp`.
The Linux/CHI404 C-lane builds the module before parity and discovers
`build/hft3_features_cpp*.so`; the Windows daily subset checks the
`build/hft3_features_cpp*.pyd` artifact.

**Build command**:

```bash
PYBIND11_DIR="$(python3 -m pybind11 --cmakedir)"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -Dpybind11_DIR="$PYBIND11_DIR"
cmake --build build --target hft3_features_cpp
```

Output artefact: `build/hft3_features_cpp*.so` on Linux/CHI404 or
`build/hft3_features_cpp*.pyd` on Windows.

Status: **BUILT and golden bit-identical** on all non-regime slots. Regime
slots 41–49 differ between Python and C++ because the Python pipeline calls
`RegimeFilter` outside the extractor (in `market_state_pipeline.py`) while
the C++ extractor integrates it internally; this architecture difference
persists until the pipeline-level integration task lands (ALPHA_CME.md M0).

**Parity driver**: `scripts/verify_cpp_parity.py` — hard-fails (exit 2) if
the C++ module is absent; runs both extractors on a real lake NPZ and prints
a per-slot diff table. CI must assert the `.so`/`.pyd` artefact is present
before invoking this script (see CORRECTNESS.md §2 row 3).

---

## 4. System Requirements for Live Hot Chain

1. CPU isolation and core map: **superseded by CHI404_RUNTIME.md §2 fused-thread
   model** — the entire compute chain runs on one busy-spinning SCHED_FIFO
   thread (core 2); no per-stage core splitting. See CHI404_RUNTIME.md §2.1
   for rationale and the authoritative core map.
2. Busy-spin consumers: no `sched_yield` or sleep in hot loop.
3. No allocation post-init: all buffers pre-allocated; `SPSCQueue` uses
   stack/pre-allocated array; `DecisionEngine` weights loaded at startup.
4. Hugepages: where supported on CHI404 kernel; reduces TLB miss latency.

Items 2–4 are listed requirements; current CHI404 configuration status is
not verified in code artifacts at time of writing.

---

## 5. Compute Placement Policy

Use all available processing power, on the box whose job it is.

1. **MSI laptop (Windows, 8C/16T, RTX 3080) = research/batch compute.**
   - Matrix sweeps: `scripts/run_event_universe.py` multiprocessing pool at
     `cores − 2` — the canonical way to saturate the box.
   - Test gates: run `pytest -n auto --dist loadfile` (pytest-xdist;
     `loadfile` groups tests per file so shared runtime artifacts don't
     race). Serial pytest leaves ~90% of the machine idle.
   - The GPU contributes nothing to this pipeline (no stage is
     GPU-accelerated; none needs to be). Constraint is per-core Python
     speed and core count, not RAM (each replay loads ~1.4 MB NPZ).
   - Caveats: laptop thermals throttle sustained all-core loads; Windows
     spawn-based multiprocessing adds startup overhead. A cheap Linux box
     beats it for large sweeps when one exists.
2. **CHI404 = latency box. Keep it clean.**
   - Bare-metal tuned for jitter (cyclictest p99 11 µs on isolated cores).
     Batch research running concurrently with latency probes or a
     paper/live session pollutes exactly the numbers the box exists to
     produce.
   - Isolated cores are reserved for the live hot chain (md callback →
     book → features → decision → risk → submit); housekeeping cores
     handle everything else.
   - Exception: when CHI404 is fully idle — not trading, not probing — it
     may run matrix sweeps (server CPU, Linux, no thermal throttle, numba
     marginally better on Linux). Never concurrently with latency
     measurement or any paper/live session.
3. **Two-machine shard procedure.**  Pass `--shard 0/2` on the laptop and
   `--shard 1/2` on CHI404 (or any I/N split). Assignment is
   `SHA-256(event_id|symbol|band_ms) % N == I` — deterministic across
   machines and independent lake scans, so both boxes agree on the
   partition without coordination.  Merge the two `universe_result.json`
   outputs offline; do not attempt live aggregation across machines.
4. **Service-check guard (CHI404 only).**  `scripts/chi404_run_universe_batch.sh`
   refuses to start if `hft3-rithmic-trial.service` or
   `hft3-paper-latency.service` is active, or if a `rithmic_latency_probe`
   process is running.  Never bypass these checks; a batch sweep running
   concurrently with latency probes invalidates the jitter measurements.
