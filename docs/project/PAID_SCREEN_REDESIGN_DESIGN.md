# Paid-Screen Redesign — Design Document

Status: **Implemented (Phases 1–5).** Production hardening (Phase 6) is covered by
`tests/test_paid_screen_hardening.py`.

Verification: **214 tests pass** across 8 test files
(`test_paid_screen_profiling`, `test_paid_screen_types`, `test_paid_screen_batch`,
`test_paid_screen_worker`, `test_paid_screen_cache`, `test_paid_screen_matrix`,
`test_paid_screen_hardening`, `test_paid_screen_parity_corpus`).

---

## 1. Motivation

The legacy paid-screen path routed work units by **parsing free-text thesis
strings** to infer the model and symbol to run. That coupling made execution
non-deterministic, prevented batching, reloaded data and VectorBT per unit,
and spawned a subprocess per unit. The redesign replaces NL routing with
**structured typed units** and a **content-addressed execution model** so that:

- The model and symbol to execute come from explicit fields, not thesis text.
- Deterministic intermediate products (NPZ → bars → features → signals → VBT
  results) are computed once and reused across compatible units.
- Long-lived worker processes amortize module imports and VectorBT/Rust
  initialization across many batches.
- A 256-combination parameter grid runs as a small number of chunked matrix
  `Portfolio.from_signals` calls instead of 256 sequential calls, with
  **byte-for-byte identical per-trial results** versus the loop mode.

## 2. Execution model

```
JSONL units ──► PaidScreenUnit (typed)
                 │  identity_hash() = sha256(model_id, symbol, event_id, hyp_id, feature_set_id)[:16]
                 ▼
group_units_by_batch_key(symbol|event_id) ──► batches
                 │
                 ▼
PaidScreenWorker.init()  [once per process: git commit, VectorBT version, Rust runtime proof]
                 │
                 ▼
screen_paid_batch(units, WorkerContext, RunProfiler, BoundedLRUCache)
   ├─ NPZ discovery: load OHLCV once for shared event_id (cache hit/miss recorded)
   ├─ resolve_model_from_registry(model_id)  ← registry, NOT thesis text
   └─ per-unit: artifact path, status (OK | OK_CACHED | ERROR | SKIPPED)
                 │
                 ▼
run_vectorbt_simulation_matrix()  [Phase 5]
   ├─ _chunk_parameter_trials(grid, chunk_size=64)
   ├─ per-column: _shift_signal_to_executable_bar + _apply_holding_period_exit
   ├─ one matrix Portfolio.from_signals call per chunk
   └─ per-column: pf[:, col].stats() → gate metrics → promote/reject
```

## 3. Modules

| Phase | Module | Role |
|------|--------|------|
| 1 | `paid_screen_profiling.py` | `RunProfiler`, `StageTimer`, `FailureDiagnostic`, `determine_manifest_status`, `write_failure_diagnostics` |
| 2 | `paid_screen_types.py` | `PaidScreenUnit`, `WorkerContext`, `UnitScreeningResult`, `BatchingKey` (+ 4 cache-key methods) |
| 2 | `paid_screen_batch.py` | `screen_paid_batch`, `resolve_model_from_registry`, `build_batching_key`, `group_units_by_batch_key` |
| 3 | `paid_screen_worker.py` | `PaidScreenWorker` long-lived worker, `worker_process_main` multiprocessing entry |
| 4 | `paid_screen_cache.py` | `BoundedLRUCache`, `CacheEntry`, 5 `compute_*_cache_key` functions |
| 5 | `paid_screen_matrix.py` | `run_vectorbt_simulation_matrix`, `_chunk_parameter_trials`, `_param_chunk_hash`, `_build_signal_matrix` |
| 6 | `test_paid_screen_hardening.py` | Failure injection, interruption/resume, corrupted cache/artifact recovery, manifest-status correctness |

## 4. Core invariants

1. **Thesis is metadata, not execution.** `PaidScreenUnit.thesis` is preserved
   for human readability; `model_id`, `symbol`, `event_id`, `feature_set_id`
   determine execution.
2. **Registry resolution, not NL parsing.** `resolve_model_from_registry`
   resolves `model_id` directly from the model registry.
3. **Batch compatibility is exact.** Two units may share a batch **iff** their
   `BatchingKey` is equal (all 15 fields — see
   `PAID_SCREEN_BATCHING_KEY_SPEC.md`).
4. **Content-addressed caching.** Every cached intermediate is keyed by the
   content + configuration that determine its value; invalidation is implicit
   (a changed source hash yields a different key, so stale entries are never
   looked up). See `PAID_SCREEN_CACHE_SPEC.md`.
5. **Loop↔matrix parity.** `run_vectorbt_simulation_matrix` reuses the exact
   no-lookahead shift, holding-period exit, gate-metric extraction, walk-forward
   simulation, candidate-ID, parameter-hash, and fail-closed logic of the loop
   mode. Per-trial promoted/rejected rows, hashes, and IDs are **identical**
   regardless of `chunk_size` (verified by `test_paid_screen_matrix.py`:
   "Results are independent of chunk size — concatenation is identical",
   "Per-trial candidate IDs are identical to loop mode", "Running twice
   produces identical ordered candidate IDs").
6. **Fail-closed.** Missing VectorBT / missing Rust runtime proof / missing gate
   stats produce explicit `RejectedCandidate` rows with named stop reasons
   (`vectorbt_unavailable_fail_closed`,
   `rust_runtime_proof_missing_fail_closed`, `vectorbt_stats_missing_gate_fields`,
   …). No silent success.
7. **Manifest honesty.** `determine_manifest_status` never returns
   `"complete"` when `failed > 0`; the process exit code and manifest status
   must agree.
8. **One NPZ load per shared event.** `screen_paid_batch` loads OHLCV once for
   the batch's shared `event_id` via `data_cache`; subsequent batches with the
   same `(events_csv_hash, event_id)` get a cache hit.
9. **Cache counters are delta-reconciled.** When a `BoundedLRUCache` is supplied,
   `screen_paid_batch` snapshots `hit_count`/`miss_count` before the batch and
   folds only the **delta** into the `RunProfiler`, so multiple batches against
   the same cache+profiler do not double-count.

## 5. Phases

- **Phase 1 — Instrumentation & correctness baseline.** `paid_screen_profiling.py`:
  stage timers, failure diagnostics, run-manifest status, failure-diagnostics
  persistence. Import-safe (no heavy deps at module level).
- **Phase 2 — Structured execution path.** `paid_screen_types.py` +
  `paid_screen_batch.py`: typed units, registry-based model resolution, batch
  entry point, first-pass `(symbol, event_id)` grouping.
- **Phase 3 — Long-lived workers.** `paid_screen_worker.py`: one-time init
  (git commit, VectorBT version, Rust runtime proof), multi-batch processing,
  bounded LRU cache, optional recycle after N batches for memory control,
  multiprocessing entry `worker_process_main`.
- **Phase 4 — Data & feature caching.** `paid_screen_cache.py`: `BoundedLRUCache`
  (max entries + max memory bytes, LRU eviction, observable hit/miss counters)
  plus 5 `compute_*_cache_key` functions for the NPZ→bars→features→signals→VBT
  chain. `screen_paid_batch` is cache-backend agnostic (plain `dict` for legacy
  callers, `BoundedLRUCache` for Phase 4).
- **Phase 5 — VectorBT matrix mode.** `paid_screen_matrix.py`: chunked matrix
  `Portfolio.from_signals` replacing 256 sequential calls; per-column shift +
  holding-period exit; per-column `pf[:, col].stats()`;
  `_param_chunk_hash` for VBT-result cache keying. Identical results to loop
  mode for the same inputs.
- **Phase 6 — Production hardening.** `test_paid_screen_hardening.py`:
  worker-crash isolation, interruption/resume with valid-skip / invalid-recompute,
  corrupted JSON artifact rejection, partial-write rejection, bounded-LRU
  recycle under memory pressure, manifest-status correctness, batching-key
  incompatibility rejection.

## 6. Architecture notes

- **WorkerContext is frozen.** Built once in `PaidScreenWorker.init()`; the same
  immutable context is reused across all batches for that worker
  (`repo_root`, `git_commit`, `screening_scope`, `vectorbt_engine`,
  `vectorbt_version`, `rust_runtime_proof`, `events_csv_hash`,
  `lake_manifest_hash`, `run_budget`).
- **Recycle, not restart.** `PaidScreenWorker._recycle()` clears the data cache
  and resets the batch counter after `max_batches_before_recycle` (default 100)
  but does **not** restart the process — modules stay imported and VectorBT
  stays initialized.
- **Cache backend choice.** `screen_paid_batch` accepts `dict | BoundedLRUCache |
  None`. `_is_bounded_lru_cache` gates the delta-reconciliation path; legacy
  `dict` callers get manual `profiler.cache_hits += 1` accounting.
- **Budget enforcement.** Matrix mode checks wall-clock and trial-count budgets
  at chunk boundaries (same semantics as loop mode's per-trial check);
  `_append_budget_skipped_trials` records skipped trials as rejected.

## 7. Recommendations

1. **Prefer `BoundedLRUCache` in production.** It bounds memory, evicts LRU,
  and exposes observable hit/miss counters that the profiler folds in
  delta-based. Plain `dict` is supported only for legacy callers.
2. **Default `chunk_size=64`.** `DEFAULT_MATRIX_CHUNK_SIZE` bounds peak memory
  (`bars × chunk_size × 8 bytes` per signal matrix) while amortizing Python
  overhead. Tune down for longer bar windows.
3. **Wire Phase 3 workers to Phase 5 matrix mode.** `screen_paid_batch` currently
  records the resolved model + artifact path (Phase 2 scope). The full
  `filter_candidates_matrix` call should be invoked from the worker path so
  long-lived workers drive matrix screening end-to-end.
4. **Run the parity corpus.** `tests/fixtures/paid_screen_parity_corpus.py`
  provides a deterministic fixed corpus for old-vs-new comparison; keep it as
  the regression anchor for the loop↔matrix parity invariant.
5. **Keep manifest status honest.** Any future orchestrator exit path must use
   `determine_manifest_status` and keep the exit code consistent with it;
   Phase 6 tests enforce this and must stay green.
6. **Content-address everything that can change semantics.** Any new
   intermediate layer must be added to the cache chain via a new
   `compute_*_cache_key` function whose inputs are the prior layer's key plus
   the hashes of whatever new content/config determines its value.

## 8. Related specifications

- `PAID_SCREEN_BATCHING_KEY_SPEC.md` — all 15 `BatchingKey` fields, equality
  semantics, compatibility rules, and worked examples.
- `PAID_SCREEN_CACHE_SPEC.md` — the 5 cache layers, key construction, source
  hashes, and invalidation rules.