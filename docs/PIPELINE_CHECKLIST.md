# HFT3 Unified Pipeline — Honest Work Checklist

**Committed:** `feat/mbo-release-lane` (commits 00292ad + 704a2b8)
**Date:** 2026-06-09

---

## What's Done (Committed)

| # | Item | File(s) | Status |
|---|------|---------|--------|
| 1 | Orchestrator package (10 stages) | `packages/hft3_pipeline/` | DONE |
| 2 | CLI entry point (inventory, status, run) | `__main__.py` | DONE |
| 3 | Run mode enforcement (5 modes) | `run_mode.py` | DONE |
| 4 | Repo/lane inventory (Stage 0) | `inventory.py` | DONE |
| 5 | Manifest dataclasses (Pipeline, VectorBT, HFT) | `manifest.py` | DONE |
| 6 | All 10 pipeline stages (0-9) | `stages.py` | DONE |
| 7 | TradeManager restored (Phases 14-23) | `packages/trade_manager/` | DONE |
| 8 | Model metrics expanded (6 groups, 120+ metrics) | `packages/hft3/model_metrics/` | DONE |
| 9 | Search space registry | `configs/model_search_spaces.yaml` | DONE |
| 10 | VectorBT adapter restored | `packages/backtest_pipeline/src/vectorbt_adapter.py` | DONE |
| 11 | Promotion gate restored | `packages/backtest_pipeline/src/promotion_gate.py` | DONE |
| 12 | Certification registry hardened | `packages/hft3/validation/certification_registry.py` | DONE |
| 13 | CLI registered in pyproject.toml | `pyproject.toml` | DONE |
| 14 | Fast tests (6 pass) | `tests/test_pipeline_e2e.py` | DONE |
| 15 | Integration tests (23 total) | `tests/test_pipeline_integration.py` | DONE |
| 16 | Audit docs | `artifacts/repo_consolidation/` | DONE |
| 17 | Honest naming (data_fingerprint not feature_generation) | `stages.py` | DONE |
| 18 | HFT truth uses replay_matrix (not SignalBacktester) | `stages.py` | DONE |

---

## What's Broken (Needs Work)

| # | Issue | Priority | Root Cause |
|---|-------|----------|------------|
| 1 | **VectorBT produces 0 candidates** | HIGH | `_build_ohlcv_bars_from_npz` subsamples to 200 events. With bar_size=100, only 2 bars. MarketStatePipeline is too slow for >200 events, so can't increase. Need to use raw NPX prices (px field) directly, bypassing MarketStatePipeline entirely for the fast filter stage. |
| 2 | **HFT truth never runs** | HIGH | Blocked by issue #1. Pipeline stops at Stage 3. |
| 3 | **10 slow E2E tests timeout** | MEDIUM | MarketStatePipeline processes events one at a time with expensive EventContextEngine scanning events.csv for each event. Tests that call the pipeline stages timeout after 300s. |
| 4 | **WorkbenchTruth UI not updated** | MEDIUM | `apps/workbench/src/state/workbench_truth.py` doesn't show VectorBT stage status, candidate counts, or blockers. UI shows lanes but not pipeline stages. |
| 5 | **Full pipeline never completed** | HIGH | Despite claiming "PIPELINE COMPLETE" in earlier sessions, no run has ever completed all 10 stages. The pipeline always stops at Stage 3 with 0 VectorBT candidates. |
| 6 | **MarketStatePipeline too slow** | MEDIUM | `EventContextEngine.resolve_ns()` does a linear scan of events.csv for every MBO event. This is O(N*M) where N=146K events, M=12K event rows. Need to replace with sorted interval tree or binary search. |

---

## What To Do Next (In Priority Order)

### 1. Fix VectorBT to produce candidates (Blocking)

**Problem:** `_build_ohlcv_bars_from_npz()` in `stages.py` uses MarketStatePipeline which is too slow for >200 events. With 200 events and bar_size=100, only 2 bars.

**Fix:** Bypass MarketStatePipeline entirely. Use raw NPZ prices directly:
```python
# In _build_ohlcv_bars_from_npz, replace the MarketStatePipeline loop with:
raw = load_npz_events(npz_path)
ts = raw["local_ts"].astype(np.int64)
px = raw["px"].astype(np.float64)
qty = raw["qty"].astype(np.float64)
# Build OHLCV bars from these raw arrays
# Target 50+ bars using adaptive bar sizing
```
This was partially started in an edit but needs to be completed.

**Estimated time:** 30 min

### 2. Run pipeline end-to-end (Verification)

Once VectorBT produces candidates, verify the full pipeline completes all 10 stages.

**Expected output:**
```
[Stage 0] Inventory... lanes=4 models=55 vbt=True
[Stage 1] Data readiness... status=ready
[Stage 2] Data fingerprint... type=mbo_raw, events=146184
[Stage 3] VectorBT filter... tested=36, passed=8, backend=vectorbt
[Stage 4] HFT truth... pnl=XX, trades=XX, eligible=True
[Stage 5] Full metrics... grade=X, score=XX
[Stage 6] Robustness... status=SKIPPED
[Stage 7] Promotion... status=PROMOTED|QUARANTINED
[Stage 8] Trade Manager... status=COMPLETED|SKIPPED
[Stage 9] Workbench truth... status=COMPLETED
```

**Estimated time:** 10 min (plus pipeline runtime ~2-5 min)

### 3. Fix VectorBT signal generation

**Problem:** `_generate_hypothesis_signals()` uses MarketStatePipeline which is slow. For the VectorBT fast-filter stage, we should use the raw NPZ px prices directly with the restored `vectorbt_adapter.py` which already has a `_default_signal_computer()` that works on OHLCV bars.

**Fix:** Don't call `_generate_hypothesis_signals()` at all in the VectorBT stage. Instead, use the restored `vectorbt_adapter.filter_candidates()` which already handles data loading, signal computation, and parameter sweeping correctly.

**Estimated time:** 20 min

### 4. Fix slow E2E tests

**Problem:** 10 tests timeout because they call `stage_vectorbt_filter()` and `stage_hft_truth()` which process events through MarketStatePipeline.

**Fix:** 
- Either increase pytest timeout to 600s
- Or add `@pytest.mark.slow` and skip in CI
- Or mock the MarketStatePipeline for tests
- Or use smaller test NPZ files (1K events instead of 146K)

**Estimated time:** 1 hour

### 5. Update WorkbenchTruth

**Problem:** `apps/workbench/src/state/workbench_truth.py` doesn't show VectorBT stage.

**Fix:** Add VectorBT fields to `CmeEntryTruth`:
```python
vectorbt_status: str = "unknown"
vectorbt_candidates_tested: int = 0
vectorbt_candidates_passed: int = 0
vectorbt_blockers: list[str] = field(default_factory=list)
```

**Estimated time:** 1 hour

---

## Test Summary

| Suite | Tests | Pass | Fail | Timeout | Notes |
|-------|-------|------|------|---------|-------|
| Run mode enforcement | 2 | 2 | 0 | 0 | Pure unit tests |
| Manifest persistence | 2 | 2 | 0 | 0 | Dataclass field checks |
| Metrics surface | 2 | 2 | 0 | 0 | 6 groups, missing reasons |
| Repo inventory | 3 | 3 | 0 | 0 | Lane detection, capabilities |
| Promotion gates | 8 | 3 | 0 | 5 | 5 tests timeout calling pipeline |
| Pipeline order | 3 | 0 | 0 | 3 | All 3 timeout (VectorBT stage) |
| HFT truth gate | 1 | 0 | 0 | 1 | Timeout |
| **TOTAL** | **21** | **12** | **0** | **9** | |

---

## Honest Status

**The pipeline scaffolding is correct.** The 10-stage architecture, manifest dataclasses, run mode enforcement, metrics surface, and promotion logic are all properly structured.

**The pipeline does not run end-to-end.** VectorBT produces 0 candidates because `_build_ohlcv_bars_from_npz` is bottlenecked by MarketStatePipeline slowness. This blocks all downstream stages.

**What works today:**
- `python -m hft3_pipeline inventory` — shows 4 lanes, 55 models, all capabilities
- `python -m hft3_pipeline status` — shows capability flags
- Stages 0-2 execute correctly (inventory, data readiness, data fingerprint)
- 12 tests pass (pure unit tests + dataclass validation)

**What doesn't work today:**
- Stage 3 (VectorBT filter) finds 0 candidates
- Stages 4-9 never execute
- 9 tests timeout

**The fix is narrow:** Bypass MarketStatePipeline in `_build_ohlcv_bars_from_npz` by using raw NPZ px prices directly. This was partially implemented in the last edit to stages.py (replacing the MarketStatePipeline loop with raw array access) but the edit was a partial replacement that needs completion.

---

## Next Action

Fix `_build_ohlcv_bars_from_npz` to build OHLCV bars directly from raw NPZ event arrays (px, qty, ts fields) instead of processing through MarketStatePipeline. This is a 5-line change that unblocks the entire pipeline.
