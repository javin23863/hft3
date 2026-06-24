# Paid-Screen Redesign — Benchmark Report

Status: **Framework complete. Benchmarks with real NPZ data are pending a Vast run.**

---

## 1. Benchmark configurations

The following configurations are defined by the redesign spec and the v2 orchestrator. All must use the **same dataset** and **same unit set** for comparison.

| Configuration | Description | Command flag |
|---------------|-------------|--------------|
| **C1 — v1 baseline** | Retired subprocess-per-unit | retired v1 runner (**deleted 2026-06**) |
| **C2 — v2 long-lived workers, no cache** | Long-lived workers, cache disabled (max_entries=1) | `--cache-max-entries 1 --cache-memory-limit-mb 1` |
| **C3 — v2 + event-data cache** | Cache NPZ→events→bars per event_id | `--cache-max-entries 1000 --cache-memory-limit-mb 4096` |
| **C4 — v2 + feature cache** | Cache features per (event_id, feature_set_hash) | C3 + feature-cache path wired |
| **C5 — v2 + full batching** | C3 + VectorBT chunked matrices | `--chunk-size 64` (default) |

Additional variants for chunk-size tuning:

| Variant | Chunk size |
|---------|-----------|
| C5-1 | 1 (baseline — one trial per call, no batching) |
| C5-8 | 8 |
| C5-64 | 64 (default) |
| C5-128 | 128 |
| C5-256 | 256 (full grid in one call) |

---

## 2. Metrics to report

Per the redesign spec §9 (benchmark requirements), report these metrics for each configuration:

### Wall-clock and throughput

```
wall_clock_seconds           total elapsed from orchestrator start to manifest write
units_completed              count of units where status != FAILED
units_failed                 count of units where status == FAILED
units_per_hour               completed / (wall_clock_seconds / 3600)
trials_completed             total parameter trials executed (units × grid_size)
trials_per_second            trials_completed / wall_clock_seconds
batches_completed            number of batches dispatched to workers
batch_size_p50               median batch size
batch_size_p95               95th percentile batch size
```

### CPU and memory

```
cpu_utilization_pct          average CPU utilization across all workers
peak_resident_memory_mb      peak RSS across all worker processes
worker_count                 number of worker processes
```

### I/O

```
disk_read_mb                 total bytes read (if measurable via /proc or psutil)
disk_write_mb                total bytes written (artifact files + manifest + diagnostics)
```

### Cache

```
cache_hit_rate               hits / (hits + misses) across all workers
cache_hits                   total cache hits
cache_misses                  total cache misses
```

### Latency

```
unit_latency_p50_seconds     median per-unit wall-clock
unit_latency_p95_seconds     95th percentile per-unit wall-clock
batch_latency_p50_seconds    median per-batch wall-clock
batch_latency_p95_seconds    95th percentile per-batch wall-clock
```

### Stage breakdown

Per-stage timing (from profiler):

```
worker_init_seconds          one-time imports + VectorBT init + Rust proof
manifest_parsing_seconds     parsing unit JSONL + grouping
npz_discovery_seconds        globbing and loading NPZ files
bar_construction_seconds     OHLCV bar aggregation
feature_construction_seconds feature plane construction
signal_construction_seconds  raw signal computation
vbt_simulation_seconds       Portfolio.from_signals (or from_signals matrix)
auxiliary_metrics_seconds    numpy metrics + walk-forward
artifact_validation_seconds  screening artifact schema check
artifact_writing_seconds     disk write + atomic rename
cache_lookup_seconds         cache key computation + lookup
cache_serialization_seconds  cache value serialization
```

### Startup overhead

```
startup_seconds              import time + Rust init (C1: per-subprocess; C2-C5: once per worker)
process_spawn_seconds        time to spawn N worker processes
```

### Reliability

```
failure_rate                 failed_units / total_units
aborted_units                units skipped due to abort/timeout
resumed_units                units skipped due to valid existing artifact (--resume)
```

---

## 3. Benchmark conditions

All benchmarks must run under identical conditions:

- **Hardware:** Same Vast.ai instance type (label pattern `hft3-m6-one-shot`, 256 vCPU).
- **Dataset:** Same `HFT3_NPZ_ROOT` and `HFT3_MANIFEST_PATH` — identical NPZ files.
- **Units:** Same JSONL manifest (the parity corpus or a full run manifest).
- **Scope:** `--vectorbt-scope paid-compute` (or `screen`/`refine` for paid-compute equivalent).
- **Workers:** Vary per configuration — C1 tested with same worker count as C2-C5 for fair comparison.
- **Cold start:** Clear OS page cache between runs (`echo 3 > /proc/sys/vm/drop_caches` as root on Linux).
- **Reproducibility:** At least 2 runs per configuration; report the median.

---

## 4. What has been measured (dry-run only)

All measurement infrastructure is in place:

- `RunProfiler` (Phase 1.1): per-stage timing, cache hit/miss counters, failure tracking, manifest summary.
- `BoundedLRUCache.hit_count` / `miss_count` (Phase 4.1): observable, delta-reconciled.
- `PaidScreenWorker.profiler` (Phase 3.1): accumulates timings across batches.
- `run_vectorbt_paid_screen_v2.py` (Phase 3.2): aggregates worker profiler summaries into the run manifest.

**No benchmarks have been run** with real NPZ data on a Vast instance. The v2 orchestrator was tested with a dry-run (`--dry-run`) and a single-worker run against nonexistent NPZ data (`--owner-waiver`), which correctly returned all ERROR results with `no_ohlcv_data`.

---

## 5. Expected improvement areas

Based on the architecture (no benchmarks yet, but these are the areas where improvement is expected):

| Area | Before (v1) | After (v2) |
|------|-------------|------------|
| Python startup per unit | 1 subprocess × ~77,952 units | 0 (long-lived workers, init once) |
| VectorBT import per unit | ~77,952 imports | ~230 imports (one per worker) |
| Rust runtime proof per unit | ~77,952 calls | ~230 calls (one per worker) |
| NPZ load per unit | ~77,952 loads | ~events_count loads (cached) |
| Signal compute per (candidate, params) | ~77,952 × |same| 256 calls | ~77,952 × |same| 256 calls |
| Portfolio.from_signals calls | ~20M sequential calls (77,952 × 256) | ~20M / chunk_size calls (matrix mode) |
| Artifact validation | ~77,952 validations | ~77,952 validations (unchanged) |
| Artifact write | ~77,952 writes | ~77,952 writes (unchanged — per-unit contract preserved) |

The dominant speedup is expected in:
1. **Startup elimination:** ~230 workers instead of ~77,952 subprocesses.
2. **Data reuse:** NPZ loads per event_id, not per unit (26 events vs 77,952 units).
3. **Matrix execution:** 256 sequential `from_signals` calls per candidate become ~4 chunked calls (chunk_size=64).

---

## 6. Chunk-size recommendation

The default `DEFAULT_MATRIX_CHUNK_SIZE = 64` was chosen because:

- 256 ÷ 64 = 4 chunks (small number of `from_signals` calls).
- Each chunk produces a [bars, 64] signal matrix — manageable memory for 1-minute bars over a typical event window (~500-2000 bars).
- At 256 (full grid in one call), the entries/exits matrix grows proportionally, and any single trial's signal computer failure requires reconstructing all 256 columns. At 64, a failure only affects that chunk.

This should be benchmarked (C5-1 through C5-256) on the target Vast instance to confirm. The chunk size is configurable via `--chunk-size` in the v2 orchestrator.

---

## 7. Final recommendation (pending benchmark)

Per the redesign spec §12, the final recommendation must include:

```
worker_count:          230 (on 256 vCPU Vast instance, leaving cores for OS/disk)
batch_size:            50 (experiment with 25-200)
chunk_size:            64 (benchmark 1, 8, 64, 128, 256)
cache_memory_mb:       4096 (experiment with 1024-8192)
worker_recycle_interval: 100 batches (experiment with 50-500)
```

These numbers are conservative defaults from the design doc. They must be adjusted based on **actual benchmark results** on the target Vast instance.

---

## 8. Gate status

```
profiling infrastructure: ✅ (RunProfiler, stage timings, failure diagnostics)
cache counter hooks: ✅ (BoundedLRUCache.hit_count/miss_count, delta-reconciled)
manifest aggregation: ✅ (v2 orchestrator collects worker summaries)
benchmark harness: ✅ (v2 orchestrator; v1 retired 2026-06)
actual benchmarks run: ❌ pending Vast + real NPZ data
chunk-size recommendation validated: ❌ pending benchmark
```

**Benchmark gate cannot close** until C1-C5 benchmarks run on identical scope with real NPZ data, and the results demonstrate a material wall-clock improvement. The test infrastructure (209 tests) confirms that the redesigned path is mechanically correct; the benchmark confirms it is faster.
