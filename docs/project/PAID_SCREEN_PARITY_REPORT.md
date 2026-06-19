# Paid-Screen Redesign — Parity Report

Status: **Framework complete. Full old-vs-new comparison with real NPZ data is pending a Vast run.**

---

## 1. What has been verified (in tests)

### 1.1 Loop↔matrix parity

The `TestRunVectorbtSimulationMatrix` class in `tests/test_paid_screen_matrix.py` (37 tests) verifies:

| Invariant | Test | Verdict |
|-----------|------|---------|
| Same result count as loop mode | `test_same_result_count_as_loop_mode` | ✅ |
| Deterministic parameter ordering | `test_parameter_ordering_is_deterministic` | ✅ |
| Chunk boundary independence | `test_chunk_boundary_independence` | ✅ |
| Metric parity across chunk sizes | `test_chunk_boundary_independence_metrics` | ✅ |
| No-lookahead shift per column | `test_no_lookahead_shift_applied_per_column` | ✅ |
| Per-column stats extraction | `test_per_column_stats_extraction` | ✅ |
| Candidate ID matches loop mode | `test_candidate_id_matches_loop_mode` | ✅ |
| Fail-closed without VectorBT | `test_fail_closed_without_vectorbt` | ✅ |
| Signal failure rejects per trial | `test_signal_failure_rejects_trial` | ✅ |
| Trial budget enforcement | `test_max_total_trials_respected` | ✅ |

These tests use a `FakeMatrixPortfolio` that mimics VectorBT's matrix API (`pf[:, i]` indexing, `.stats()` per column). The fake VBT returns deterministic values, so the tests verify the *mechanical* equivalence — that the same parameter combinations reach the same code paths, in the same order, with the same identity hashes, regardless of chunk size.

### 1.2 Chunk boundary independence

The matrix mode splits 256 parameter combinations into chunks (default size 64). `_chunk_parameter_trials` yields contiguous slices; concatenation reproduces the full list exactly. The `test_chunk_boundary_independence` test runs the same inputs with `chunk_size=1`, `8`, `64`, `256`, and `1000`, then asserts identical ordered result lists. This proves that chunk boundaries do not reorder, duplicate, or drop trials.

### 1.3 `_build_sl_tp_arrays` parity fix

Pass B review found a latent `stop_loss_pct == 0.0` falsiness divergence between loop mode and matrix mode. Fixed in commit `c6c537d1`: `_build_sl_tp_arrays` now uses `if stop_loss` (falsy for 0.0) instead of `if stop_loss is not None`, matching the loop mode's `stop_loss_f if stop_loss_f else None`. This is verified by `test_correct_values` and `test_divides_by_100` in the matrix tests.

### 1.4 Budget enforcement

Loop mode checks trial-count and wall-clock budgets *every trial*. Matrix mode checks trial-count both at chunk boundaries and mid-chunk (per-trial inside surviving-chunk collection). Wall-clock is checked at chunk boundaries (not mid-chunk), which means matrix mode can overshoot the wall-clock budget by up to `chunk_size - 1` trials' worth of compute. This is documented in the design doc as an accepted coarse-grained boundary and does not affect result correctness for executed trials.

### 1.5 Batching key correctness

`TestBatchingKey` in `tests/test_paid_screen_types.py` (33 tests) verifies that all 15 fields contribute to equality, and that changing any field produces a different key. `TestBatchingKeyMismatch` in `tests/test_paid_screen_hardening.py` verifies that different symbols/events do not batch together.

### 1.6 Cache key correctness

`TestCacheKeyConstruction` and `TestCacheInvalidation` in `tests/test_paid_screen_cache.py` (31 tests) verify that all 5 cache layers (NPZ→bars→features→signals→VBT) produce different keys when any content or configuration determinant changes.

---

## 2. Parity corpus

| Dimension | Count |
|-----------|-------|
| Total units | 933 |
| Unique events | 26 |
| Unique symbols | 7 (all CME M6) |
| Unique models | 25 (HYP_1 through HYP_25) |
| Expected outcomes: ok | 860 |
| Expected outcomes: missing_model | 70 |
| Expected outcomes: missing_data | 2 |
| Expected outcomes: budget_exhausted | 1 |

The corpus is defined in `tests/fixtures/paid_screen_parity_corpus.py` and verified by 16 tests in `tests/test_paid_screen_parity_corpus.py`. It covers all required dimensions from the redesign spec:

- ✅ At least 20 events (26 events: CPI, NFP, FOMC, GDP, PCE, ISM, RETAIL, JOBLESS)
- ✅ All 7 CME M6 symbols (MES, MNQ, ES, NQ, ZN, ZB, RTY)
- ✅ At least 20 models (25 models: HYP_1 through HYP_25)
- ✅ Sparse-data events (JOBLESS, RETAIL, ISM)
- ✅ Dense-data events (CPI, NFP, FOMC)
- ✅ Missing-data outcomes (GDP_2025_01_30_TIGHT, PCE_2024_12_20_TIGHT)
- ✅ Missing-model outcomes (HYP_24, HYP_25 — 70 units)
- ✅ Budget exhaustion outcome (1 unit)
- ✅ Promoted/rejected candidates (exercise the full classification)

---

## 3. Comparison fields and tolerances

When full old-vs-new parity comparison runs with real NPZ data, the following must be compared:

### Exact comparison (must be byte-identical)

```
- unit_id
- model_id
- hyp_id
- symbol
- event_id
- parameter_space_id
- parameter_space_hash
- candidate_id
- strategy_params (dict)
- promotion decision (boolean)
- rejection decision (boolean)
- rejection_reason (string)
- trade_count (as vectorbt_metrics.num_trades — integer)
- signal_threshold (float, but deterministic → exact)
- holding_period_bars (integer)
- data_manifest_hash
- lake_manifest_hash
- research_clock
- split_scheme_id
```

### Tolerance-bounded comparison (floating-point in VBT stats)

```
- gross_return / net_return: tolerance 1e-4
- expectancy: tolerance 1e-4
- profit_factor: tolerance 1e-4
- Sharpe ratio: tolerance 1e-2
- max_drawdown: tolerance 1e-3
- walk_forward.is_expectancy: tolerance 1e-4
- walk_forward.oos_expectancy: tolerance 1e-4
```

### Must match exactly (no tolerance)

```
- screening_artifact_hash (computed over canonical JSON — must be identical)
- artifact validation result (pass/fail)
- feature_hashes
- data_hashes
```

---

## 4. What remains to be run

### 4.1 Full old-vs-new parity run

A Vast.ai instance with real NPZ data must:

1. Run archived v1 manifests on the parity corpus (v1 script **retired 2026-06**)
2. Run `run_vectorbt_paid_screen_v2.py` (v2) on the same parity corpus
3. Compare all 933 unit results across the fields listed in §3
4. Report any mismatches with unit-level detail

Command templates are in `PAID_SCREEN_OPS_COMMANDS.md` §2 (Parity run).

### 4.2 Real VBT parity (no fake)

The 37 matrix tests use a `FakeMatrixPortfolio`. A production parity gate should also run a small subset of the corpus against the real `vectorbt.Portfolio.from_signals` (both loop and matrix mode) and compare VBT stats numerically. The `DEFAULT_MATRIX_CHUNK_SIZE = 64` default was chosen as a conservative starting point for this verification.

### 4.3 Stop-loss/take-profit parity with real VBT

The `_build_sl_tp_arrays` fix (0.0 → None falsiness alignment) needs verification with real VBT stops — the current tests use fake portfolios. A production gate should verify that stop-loss and take-profit behavior is identical between loop and matrix mode for the full parameter grid.

---

## 5. Gate status

```
loop↔matrix mechanical parity: ✅ verified (37 tests)
chunk boundary independence: ✅ verified
candidate ID parity: ✅ verified
budget enforcement parity: ✅ verified (trial count; wall-clock coarse-grained, documented)
sl/tp falsiness parity: ✅ fixed and tested
real VBT numerical parity: ⏳ pending (needs Vast + real NPZ data)
full corpus parity (933 units): ⏳ pending (needs Vast + real NPZ data)
production parity gate: ❌ not yet run
```

**Parity gate cannot close:** The mechanical equivalence is proven in tests. Numerical equivalence with the real VectorBT engine and real NPZ data requires a Vast run. Until that run produces 100% parity across the 933-unit corpus, the migration gate should remain open.