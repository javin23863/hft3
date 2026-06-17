# VectorBT-to-HftBacktest Filtering Pipeline

## Overview

Official `polakowo/vectorbt` is the workbench source of truth for first-pass
vectorized screening. Pin and evaluate `vectorbt==1.0.0`. The target engine for
broad `screen`/`refine` runs and paid-compute sweeps is `vectorbt[rust]`; the
non-Rust path is pilot/schema proof only unless the owner explicitly accepts a
bounded diagnostic after measurement.

VectorBT sits **after** signal/hypothesis generation but **before** expensive
execution simulation. It cheaply rejects weak parameter combinations at scale.
Only validated VectorBT screen-passed candidates reach HftBacktest/replay
realism gates.

Implementation contract: [../project/VECTORBT_SCREENING_ENGINE_SPEC.md](../project/VECTORBT_SCREENING_ENGINE_SPEC.md)
is the checklist for the first build. The engine of record is deterministic
hft3 orchestration around official VectorBT APIs, with the Rust engine required
for broad screening/refine/paid-compute scopes; Gemma/local LLM outputs are
proposal artifacts only and cannot execute parameter search, select final
parameters, promote candidates, or override robustness gates.

Licensing note: vectorbt is distributed under terms that include Commons
Clause restrictions. Treat adoption as blocked on legal/operational review
before commercial or production use.

Anti-lookahead rule: if a signal is generated from a bar close, the trade must
execute on a later executable price/bar. Same-close signal generation and
execution is prohibited unless the signal timestamp is demonstrably earlier
than the executable price in the filtration.

## Pipeline Flow

```
1. run_pipeline.py --thesis "..." --event-id CPI_2024_09_11_TIGHT --vectorbt
2.   → parse_hypothesis()                [existing, unchanged]
3.   → generate_candidates(expand_for_vectorbt=True)  [expanded param grid]
4.   → VectorBTFilter.filter_candidates() [cheap OHLCV-level rejection]
5.   → ScreenGate/PromotionGate evaluation [screen thresholds only]
6.   → Rejected candidates → logged       [with reason]
7.   → Screen-passed candidates → serialized [terminal screening_artifact.json]
8.   → Default: stop fail-closed until explicit HftBacktest opt-in
9.   → --hftbacktest-realism → official HftBacktest source-lock/replay gates
10.  → Cockpit observes screening, robustness, replay, and promotion gates separately
```

## CLI Commands

```bash
# VectorBT screening artifact, then stop until explicit HftBacktest opt-in
python scripts/run_pipeline.py --thesis "Fade CPI blowout" --event-id CPI_2024_09_11_TIGHT --vectorbt

# Integrated VectorBT -> HftBacktest realism handoff
python scripts/run_pipeline.py --thesis "Fade CPI blowout" --event-id CPI_2024_09_11_TIGHT --vectorbt --hftbacktest-realism --hftbacktest-data-npz <validated_hftbacktest_npz> --hftbacktest-latency-model <latency_model.json> --hftbacktest-fill-queue-model <fill_queue_model.json> --hftbacktest-upstream-ref v2.4.2 --native-hot-path-evidence <native_cpp_latency_evidence.json#sha256:digest>

# VectorBT-only mode (no HftBacktest)
python scripts/run_pipeline.py --thesis "Fade CPI blowout" --event-id CPI_2024_09_11_TIGHT --vectorbt-only

# Model×Symbol sweep with pre-filter
python scripts/run_model_symbol_sweep.py --sweep --vectorbt-pre-filter

# Downstream HftBacktest realism handoff (official replay, fail-closed gates)
python scripts/run_hftbacktest_realism.py --screening-artifact research_cards/pipeline_runs/<run_id>/screening_artifact.json --data-npz <validated_hftbacktest_npz> --latency-model <latency_model.json> --fill-queue-model <fill_queue_model.json> --hftbacktest-upstream-ref v2.4.2 --native-hot-path-evidence <native_cpp_latency_evidence.json#sha256:digest>

# Retired hft3 replay scripts are not valid substitutes.
```

## Candidate Screen Gate

A candidate can be screen-passed when all numeric VectorBT thresholds are
satisfied:

| Threshold | Default | Purpose |
|-----------|---------|---------|
| `min_oos_expectancy` | 0.0 | Out-of-sample expectancy must be positive |
| `min_walk_forward_consistency` | 0.5 | Fraction of walk-forward windows with positive OOS |
| `max_turnover_pct` | 200% | Prevent excessive churn |
| `max_drawdown_pct` | -30% | Max acceptable peak-to-trough decline |
| `min_trades` | 10 | Minimum trades for statistical significance |
| `param_stability_rtol` | 30% | Parameter sensitivity tolerance |
| `max_slippage_sensitivity` | 0.5 | Robustness to slippage assumptions |

Hard blockers for replay eligibility:

- WFC must pass when a candidate is marked replay-eligible.
- DSR must pass when required by the robustness tier.
- PBO/CSCV must pass when required by the robustness tier.
- Missing, stale, malformed, or `not_run` robustness evidence is non-GREEN.
- A VectorBT screen pass is not live/paper promotion and is not execution
  certification.

VectorBT screen-passed candidates are serialized into the terminal handoff
artifact at `research_cards/pipeline_runs/<run_id>/screening_artifact.json`.
Only rows inside that screening artifact, with valid robustness evidence, can
reach HftBacktest/replay realism gates.

Minimum top-level screening artifact fields are listed below. The authoritative
full schema, including per-candidate fields and fail-closed robustness semantics,
lives in
[../project/VECTORBT_SCREENING_ENGINE_SPEC.md](../project/VECTORBT_SCREENING_ENGINE_SPEC.md).

```text
run_id
code_commit
screening_backend=vectorbt
vectorbt_version
vectorbt_engine=rust|numba|auto
engine_parity_status
rust_engine_required_for_scope
rust_engine_available
screening_artifact_hash
parameter_space_id
parameter_space_hash
max_trials
trials_run
run_budget_id
max_total_trials
candidate_ids
candidate_reasons
promoted_ids
promoted_reasons
rejected_ids
rejected_reasons
no_lookahead_signal_shift_proof
license_review
workbench_run_id
feature_set_id
feature_set_hash
events_csv_hash
lake_manifest_hash
split_scheme_id
stop_reasons
created_at_utc
```

HftBacktest is the downstream realism source of truth for validated VectorBT
screen-passed candidates; see
`docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md`. Existing repo-local replay
paths such as `replay_matrix`, `ReplaySession`, `run_event_replay.py`, and
`scripts/run_event_universe.py` are retired for this implementation and must not
be used as substitutes for the new official-HftBacktest-backed realism runner.
Execution-realism evidence must also preserve hft3 native hot-path proof:
latency/risk/feature hot paths are C++/native artifacts, while workstation
Python replay is research-only unless the realism spec's native-hot-path fields
are satisfied.

## Asset-Class Routing

| Asset Class | Data Source | VectorBT | HftBacktest | Execution Validate |
|---|---|---|---|---|
| CME futures (ES, NQ, MES, MNQ, ZN, ZB) | MBO NPZ → bars | Yes | Yes | Yes |
| Crypto lane | Moved to `hft3-crypto-lane`; historical OHLCV normalized path only | Out of this repo | No | Marked NO_EXECUTION_VALIDATION in its own repo |
| Equities lane | Moved to `hft3-equities-lane`; historical bar/decadal path only | Out of this repo | No | Marked NO_EXECUTION_VALIDATION in its own repo |
| Options lane | OHLCV + chain | Yes | No | Marked NO_EXECUTION_VALIDATION |

Candidates without tick/book data are marked `NO_EXECUTION_VALIDATION` rather than
pretending the test passed.

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
| `packages/backtest_pipeline/src/vectorbt_adapter.py` | Core adapter: runs VectorBT grid, produces screen-passed/rejected lists |
| `packages/backtest_pipeline/src/promotion_gate.py` | Screening artifact and gate thresholds; not live/paper promotion |
| `packages/backtest_pipeline/src/asset_class_routing.py` | Validation path per asset class |
| `research_pipeline/evaluation.py` | Inserted VectorBT pre-filter before WorkbenchEngine |
| `research_pipeline/model_generation.py` | Expanded param grid for VectorBT |
| `scripts/run_pipeline.py` | `--vectorbt`, `--vectorbt-only`, and explicit `--hftbacktest-realism` handoff flags |
| `scripts/run_model_symbol_sweep.py` | `--vectorbt-pre-filter` flag |
