# VectorBT-to-HftBacktest Filtering Pipeline

## Overview

VectorBT runs on every `scripts/run_pipeline.py` execution after signal/hypothesis
generation and before expensive execution simulation. It cheaply rejects weak
parameter combinations at scale.
Only promoted candidates reach HftBacktest replay.

## Pipeline Flow

```
1. run_pipeline.py --thesis "..." --event-id CPI_2024_09_11_TIGHT --lane cme
2.   → parse_hypothesis()                [existing, unchanged]
3.   → generate_candidates(expand_for_vectorbt=True)  [expanded param grid]
4.   → VectorBTFilter.filter_candidates() [mandatory cheap OHLCV-level rejection]
5.   → PromotionGate evaluation           [NEW: configurable thresholds]
6.   → Rejected candidates → logged       [NEW: with reason]
7.   → Promoted candidates → serialized   [NEW: full metadata artifact]
8.   → evaluate_model() → WorkbenchEngine [existing, fewer inputs]
9.   → HftBacktest replay (ReplaySession) [existing, unchanged]
10.  → deploy_best() → research_card      [existing, unchanged]
```

## CLI Commands

```bash
# Full pipeline with mandatory idea generation and VectorBT pre-filter
python scripts/run_pipeline.py --thesis "Fade CPI blowout" --event-id CPI_2024_09_11_TIGHT --lane cme

# Automation lanes: cme, equities, crypto
python scripts/run_pipeline.py --thesis "..." --event-id EVTID --lane crypto

# Model×Symbol sweep with pre-filter
python scripts/run_model_symbol_sweep.py --sweep --vectorbt-pre-filter

# Existing backtest commands — unchanged
python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT
python scripts/run_pipeline.py --thesis "..." --event-id CPI_2024_09_11_TIGHT
```

Idea generation also runs on every pipeline execution. `--vectorbt`,
`--vectorbt-only`, and `--idea-set` remain accepted for automation compatibility
but are ignored. `--vectorbt-only` no longer exits after VectorBT.

## Candidate Promotion

A candidate passes the promotion gate when all thresholds are satisfied:

| Threshold | Default | Purpose |
|-----------|---------|---------|
| `min_oos_expectancy` | 0.0 | Out-of-sample expectancy must be positive |
| `min_walk_forward_consistency` | 0.5 | Fraction of walk-forward windows with positive OOS |
| `max_turnover_pct` | 200% | Prevent excessive churn |
| `max_drawdown_pct` | -30% | Max acceptable peak-to-trough decline |
| `min_trades` | 10 | Minimum trades for statistical significance |
| `param_stability_rtol` | 30% | Parameter sensitivity tolerance |
| `max_slippage_sensitivity` | 0.5 | Robustness to slippage assumptions |

Promoted candidates are serialized to `research_cards/promotion/<candidate_id>.json`.
Only promoted candidates can reach HftBacktest.

## Asset-Class Routing

| Asset Class | Data Source | VectorBT | HftBacktest | Execution Validate |
|---|---|---|---|---|
| CME futures (ES, NQ, MES, MNQ, ZN, ZB) | MBO NPZ → bars | Yes | Yes | Yes |
| Crypto lane | OHLCV normalized | Yes | No | Crypto execution validation |
| Equities lane | Bar/decadal | Yes | No | Marked NO_EXECUTION_VALIDATION |
| Options lane | OHLCV + chain | Yes | No | Marked NO_EXECUTION_VALIDATION |

Promoted `asset_class=CRYPTO` candidates go through crypto execution validation
after VectorBT. Other candidates without tick/book data are marked
`NO_EXECUTION_VALIDATION` rather than pretending the test passed.

## Interpreting VectorBT vs HftBacktest Results

For every candidate that reaches HftBacktest, the comparison report shows:

- VectorBT signal-level return (OHLCV bar simulation)
- HftBacktest execution-adjusted return (tick replay with fills)
- Execution degradation (difference between the two)
- Fill rate, missed fill rate, latency sensitivity, queue-position sensitivity
- Adverse selection, realized transaction costs

A candidate that performs well in VectorBT but fails in HftBacktest is marked
`execution-failed` — not `strategy-passed`.

## Implementation Files

| File | Purpose |
|------|---------|
| `packages/backtest_pipeline/src/vectorbt_adapter.py` | Core adapter: runs VectorBT grid, produces promoted/rejected lists |
| `packages/backtest_pipeline/src/promotion_gate.py` | Promotion artifact and gate thresholds |
| `packages/backtest_pipeline/src/asset_class_routing.py` | Validation path per asset class |
| `research_pipeline/evaluation.py` | Inserted VectorBT pre-filter before WorkbenchEngine |
| `research_pipeline/model_generation.py` | Expanded param grid for VectorBT |
| `scripts/run_pipeline.py` | Mandatory VectorBT pre-filter; compatibility flags are ignored |
| `scripts/run_model_symbol_sweep.py` | `--vectorbt-pre-filter` flag |
