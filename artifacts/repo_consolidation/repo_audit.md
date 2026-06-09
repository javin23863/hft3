# HFT3 Repo Consolidation Audit

**Date:** 2026-06-09  
**Branch:** feat/mbo-release-lane  
**Commit:** 1cb8503

---

## 1. VectorBT Status

| Item | Status |
|------|--------|
| On current branch | **NO** |
| In git history | **YES** |
| Installed | **NO** |
| Source commits | `a1b4eadf`, `2b804e02`, `d71c31e8` |
| Source branch | `remotes/origin/mandatory-vectorbt-idea-crypto-lane` |

### Files to Restore
- `packages/backtest_pipeline/src/vectorbt_adapter.py`
- `packages/backtest_pipeline/src/promotion_gate.py`
- `tests/test_vectorbt_adapter.py`
- `docs/human/VECTORBT_PIPELINE.md`

### Action Required
1. Restore files from commit `a1b4eadf7af7ec8087b476a1263e74eb3c37a8aa`
2. Install vectorbt: `pip install vectorbt`
3. Add to requirements

---

## 2. Backtest Path (OPERATIONAL)

### Primary Engine
`packages/backtest_pipeline/`

### Key Files
| File | Purpose |
|------|---------|
| `src/runner.py` | HftBacktest 2.x replay runner |
| `src/replay_matrix.py` | ReplaySession-backed per-hypothesis matrix |
| `src/hft_backtest_builder.py` | Shared HftBacktest asset builder |
| `src/hypothesis_replay_strategy.py` | Strategy wrappers for replay |
| `packages/replay/replay_session.py` | Core replay session orchestrator |
| `packages/replay/market_data_adapter.py` | MBO event → MarketState bridge |
| `packages/features_engine/src/pipeline/market_state_pipeline.py` | Feature pipeline |

### Dependencies
- hftbacktest >= 2.4.0
- numpy, numba, pandas

---

## 3. Model Metrics (MINIMAL)

### Location
`packages/hft3/model_metrics/`

### Current State
- `MetricValues` dataclass (23+ fields)
- `ModelScorecard` with 7 categories
- `calculate_metric_values()` function
- `generate_model_scorecard()` function

### Missing (Needs Expansion)
- Full net_alpha_quality group (cagr, information_ratio, alpha_t_stat)
- Full drawdown_loss_behavior group (ulcer_index, drawdown_velocity, VaR/CVaR variants)
- Full robustness_stability group (PBO, deflated_sharpe_ratio, fold dispersion)
- Full execution_realism group (tick_to_send_us, all latency breakdowns)
- Full portfolio_fit group (factor_exposures, beta, crowding_overlap)
- Full prediction_calibration_quality group (ICIR, ROC_AUC, PR_AUC, F1)

---

## 4. Promotion/Certification (OPERATIONAL)

### Location
`packages/hft3/validation/`

### Key Files
| File | Purpose |
|------|---------|
| `certification_registry.py` | CertificationRecord, PromotionRecord, save_promotion |
| `certification_runner.py` | Runs certification suite |
| `promotion_gate.py` | Promotion threshold checks |
| `registry_errors.py` | Registry exception classes |

### Status
Fully operational with JSONL audit log, hash chain, file locks.

---

## 5. TradeManager (OPERATIONAL)

### Location
`packages/trade_manager/`

### Phases Implemented
- Phase 14: Registry-to-TM handoff (`manager.py`)
- Phase 15: Signal ingress (`signals.py`)
- Phase 16: Order-intent schema (`order_intent.py`)
- Phase 17: Risk-decision layer (`risk_layer.py`)
- Phase 18: Order-state machine (`order_state.py`)
- Phase 19: Execution boundary (`execution_boundary.py`)
- Phase 20: Position monitor (`monitor.py`)
- Phase 21: Kill switch (`kill_switch.py`)
- Phase 23: Session reporting (`session.py`)

### Status
Restored from git commit `a3f1c29`. All imports verified working.

---

## 6. Workbench UI (OPERATIONAL)

### Location
`apps/workbench/`

### Entry Point
`apps/workbench/ui/app.py` → Streamlit Lane Command Center

### Truth Module
`apps/workbench/src/state/workbench_truth.py`

### Truth Classes
- `WorkbenchTruth` - top-level container
- `LaneTruth` - per-lane status
- `CmeEntryTruth` - CME futures entries
- `EquitiesEntryTruth` - equities sessions
- `OptionsEntryTruth` - options groups
- `CryptoEntryTruth` - crypto venues

### Missing
- VectorBT stage in truth
- Pipeline stage tracking
- Run mode tracking

---

## 7. Lanes

| Lane | Package | Status |
|------|---------|--------|
| CME Futures | `packages/backtest_pipeline`, `packages/features_engine` | OPERATIONAL |
| Equities Low-Float | `packages/equities_lane` | OPERATIONAL |
| Options/Parity | `packages/options_lane` | OPERATIONAL |
| Crypto | `packages/crypto_lane` | OPERATIONAL |
| MBO Release | `packages/mbo_release_lane` | OPERATIONAL |

---

## 8. Gaps Preventing One Coherent Program

| ID | Severity | Description | Resolution |
|----|----------|-------------|------------|
| GAP-001 | HIGH | VectorBT not installed, not on branch | Restore + install |
| GAP-002 | HIGH | No unified CLI orchestrator | Create hft3_pipeline package |
| GAP-003 | MEDIUM | model_metrics incomplete | Expand to full 6-group surface |
| GAP-004 | MEDIUM | No shared search space registry | Create configs/model_search_spaces.yaml |
| GAP-005 | MEDIUM | WorkbenchTruth missing VectorBT stage | Add VectorbtStageTruth |
| GAP-006 | MEDIUM | No run mode enforcement | Add RunMode enum |
| GAP-007 | LOW | Missing acceptance tests | Add test_pipeline_integration.py |

---

## 9. Implementation Plan

### Phase A: Restore VectorBT
1. `git checkout a1b4eadf -- packages/backtest_pipeline/src/vectorbt_adapter.py packages/backtest_pipeline/src/promotion_gate.py`
2. `pip install vectorbt`
3. Verify imports work

### Phase B: Create Orchestrator
1. Create `packages/hft3_pipeline/` package
2. Implement `__main__.py` with all CLI commands
3. Wire to existing packages

### Phase C: Expand Metrics
1. Add all 6 metric groups to `packages/hft3/model_metrics/schemas.py`
2. Add missing_reason tracking for all metrics

### Phase D: Search Space Registry
1. Create `configs/model_search_spaces.yaml`
2. Link VectorBT and HFTBacktest to same parameter sets

### Phase E: WorkbenchTruth Enhancement
1. Add `VectorbtStageTruth` to workbench_truth.py
2. Add pipeline stage tracking

### Phase F: Run Mode Enforcement
1. Add `RunMode` enum
2. Block promotion for FIXTURE_CI, DEBUG, PERFORMANCE_BENCHMARK

### Phase G: Acceptance Tests
1. Create `tests/test_pipeline_integration.py`
2. Verify all acceptance criteria

---

## 10. Files to Create/Modify

### New Files
- `packages/hft3_pipeline/__init__.py`
- `packages/hft3_pipeline/__main__.py`
- `packages/hft3_pipeline/inventory.py`
- `packages/hft3_pipeline/stages.py`
- `packages/hft3_pipeline/run_mode.py`
- `configs/model_search_spaces.yaml`
- `tests/test_pipeline_integration.py`

### Modified Files
- `packages/hft3/model_metrics/schemas.py` (expand)
- `apps/workbench/src/state/workbench_truth.py` (add VectorBT stage)
- `pyproject.toml` (add vectorbt dependency)

### Restored Files
- `packages/backtest_pipeline/src/vectorbt_adapter.py`
- `packages/backtest_pipeline/src/promotion_gate.py`
