# Autoresearch Performance Audit — Phase 5 (2026-06-20)

Assignment: `docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md` §19  
Branch: `cursor/vast-vbt-workflow`  
Prior commits: `c90bd870` (Phase 4 completion/resume), `f5c08439` (fail-closed resume)

## Executive summary

Phase 5 wires the **autoresearch VectorBT gate** and the **Vast paid-screen v2 path** to the same optimized in-process matrix engine. There is **no `run_pipeline.py` subprocess per screening unit** on either path. Performance counters are recorded in screening artifacts (autoresearch) and run manifests (Vast v2).

| Path | Before (gap) | After (current) |
|------|----------------|-----------------|
| Autoresearch `_run_vectorbt_screen` | `filter_candidates` → loop `_run_vectorbt_simulation` (256 sequential portfolio calls per candidate) | `filter_candidates` → `run_vectorbt_simulation_matrix` with raw-signal reuse + chunked portfolios |
| Vast paid screen v2 | WIP: subprocess git per batch, fs_v1 reload per batch, chunk_size=64 | Long-lived workers, fs_v1 cache per batching key, chunk_size=256, native thread limits in manifest |
| HftBacktest autoresearch | Already uses `run_hftbacktest_campaign` with prepared-data cache | Documented below; no subprocess per scenario |

## VectorBT — autoresearch path

### Entrypoints

| Role | File | Function |
|------|------|----------|
| Generation coordinator | `packages/research_pipeline/generation_loop.py` | `_run_vectorbt_screen` |
| Canonical screen API | `packages/backtest_pipeline/src/vectorbt_adapter.py` | `filter_candidates` |
| Matrix engine | `packages/backtest_pipeline/src/paid_screen_matrix.py` | `run_vectorbt_simulation_matrix` |

### Wiring (Phase 5 change)

`filter_candidates` now delegates simulation to `run_vectorbt_simulation_matrix` (same engine as `paid_screen_batch.screen_paid_batch`). Results include `screen_performance` on the screening artifact:

```json
{
  "screening_path": "matrix_v2",
  "subprocess_per_unit": 0,
  "feature_store_load_count": 1,
  "raw_signal_computations": 1,
  "portfolio_call_count": 2,
  "matrix_chunk_size": 256,
  "native_thread_limits": {"OMP_NUM_THREADS": "1", ...}
}
```

`screen_performance` is excluded from `screening_artifact_hash` (diagnostic/runtime metadata).

### Subprocess policy

| Operation | Subprocess? | Notes |
|-----------|-------------|-------|
| Per-unit VectorBT screen | **No** | In-process matrix path |
| `run_pipeline.py` per unit | **No** | Not invoked by autoresearch screen |
| Git HEAD for provenance | Optional once | Filesystem `.git/HEAD` read in paid-screen batch; autoresearch uses adapter helper |

### Counters (evidence fields)

| Counter | Autoresearch | Paid-screen v2 worker |
|---------|--------------|----------------------|
| `feature_store_load_count` | Set when fs_v1 context resolves | `_get_or_load_fs_v1_context` + profiler |
| `raw_signal_computations` | Matrix path raw-signal cache | Same matrix module |
| `portfolio_call_count` | Chunked `Portfolio.from_signals` | Same |
| `native_thread_limits` | `apply_native_thread_limits(1)` at filter time | Worker `init()` + manifest |

## VectorBT — Vast paid-screen v2 path

Integrated prior WIP (reconciled, not discarded):

- `paid_screen_batch.py`: fs_v1 context cache, filesystem git commit, DEFAULT_MATRIX_CHUNK_SIZE=256
- `paid_screen_matrix.py`: raw signal once per recipe, performance counter hooks, allowed chunk sizes {128,256,512,1024}
- `paid_screen_worker.py`: native thread limits, no subprocess git per batch
- `paid_screen_profiling.py`: `PaidScreenPerformanceCounters`, `apply_native_thread_limits`
- `run_vectorbt_paid_screen_v2.py`: manifest records `performance_counters` + `native_thread_limits`
- `vectorbt_adapter.py`: `compute_raw_hypothesis_signal_series` extracted for reuse
- `fs_v1_screen_path.py`: cross-asset import fail-soft for offline hosts

## HftBacktest — autoresearch path

| Requirement | Status | Authority |
|-------------|--------|-----------|
| Prepared data reused | **Yes** — `hft_campaign/worker.py` `prepared_data_cache` keyed by hash | `packages/backtest_pipeline/src/hft_campaign/runner.py` |
| Feature timeline reused | **Yes** — `feature_timeline_cache` in worker context | Same |
| Long-lived worker processes | **Yes** — `HftCampaignConfig.workers` | `generation_loop._run_hft_for_candidates` |
| Fresh engine per scenario | **Yes** — new engine instance per scenario inside worker | `hft_campaign/worker.py` |
| Event-driven stepping | **Yes** — HftBacktest event loop (not bar stub) | `HFTBACKTEST_REALISM_ENGINE_SPEC.md` |
| Individual replay before combined | **Campaign manifest order** — scenarios run per candidate | `generate_scenario_manifest` |

### Benchmark methodology (identical-scope projection)

Full 72,950-unit paid run is **not** executed on the dev workstation. Method:

1. **Synthetic 500-unit fixture** — `TestBenchmarkProjection.test_project_full_run_from_synthetic_500_unit_benchmark` linearly scales worker throughput.
2. **Counter proof** — unit tests assert `subprocess_per_unit=0`, fs_v1 load count, raw-signal reuse, portfolio chunk counts.
3. **HftBacktest** — reuse counters (`prepared_data_reuse_count`) emitted in campaign profiling; full identical-scope HBT benchmark deferred to CHI404/Vast hosts with real NPZ.

Projected full-run artifact shape: `paid_screen_benchmark_projection.json` (test tmp fixture; production manifest: `runtime/reports/vast_throughput_manifest.json` when Vast job completes).

## Tests (verify commands)

```powershell
$env:PYTHONPATH=".;packages;apps"
.venv\Scripts\python.exe -m pytest tests/research_pipeline/test_autoresearch_vectorbt_performance.py tests/test_paid_screen_performance.py -q
.venv\Scripts\python.exe -m pytest tests/research_pipeline/ -q
```

### Measured results (2026-06-20, workstation)

| Command | Exit | Summary |
|---------|------|---------|
| `pytest tests/research_pipeline/test_autoresearch_vectorbt_performance.py tests/test_paid_screen_performance.py -q` | 0 | 11 passed |
| `pytest tests/research_pipeline/ -q` | 0 | 221 passed |

## Remaining gaps (Phase 6+)

- Greptile PR GrepLoop not run (`merge-ready: no` per assignment)
- Three-generation unattended acceptance run (§21)
- Full-scope repo pytest / T0 gate not re-run this phase
- Live Vast 72,950-unit throughput benchmark (projection only on workstation)
- `test_paid_screen_matrix.py::TestSlTpForPortfolio::test_numba_ndarray_width_matches_matrix_cols_not_bars` fails on installed vectorbt 1.x in venv (pre-existing; not introduced by Phase 5 diff)

## Validation honesty

```
merge-ready: no
scope-green: no (research_pipeline subset only)
scope: tests/research_pipeline/ + tests/test_paid_screen_performance.py
verify-run: exit 0 — 221 passed (research_pipeline/)
data-mode: offline / synthetic fixtures
known-gaps: Greptile, three-gen run, full-repo pytest, live Vast benchmark
```
