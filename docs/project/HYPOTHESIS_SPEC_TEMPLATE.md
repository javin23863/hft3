# Hypothesis Spec Template (math-first intake)

Every NEW hypothesis entering the self-learning loop must carry a spec
answering, before any backtest: *how, mathematically, does this express a
market mechanism that achieves the stated outcome after costs?* A hypothesis
without a falsifiable, cost-aware prediction is not testable evidence — it is
curve-fitting fuel.

The existing 50 registry hypotheses already satisfy section 2 (their signal
formulas live in `packages/features_engine/src/hypotheses/modules.py`);
backfilling sections 1/3/4/5 for them is Stage D work.

## Required sections

### 1. Market mechanism (one paragraph)
Who is on the other side of this trade and why do they systematically pay us?
Name the actor (stop-loss cascades, forced prop flattening, retail micro lag,
dealer hedging) and the constraint that makes their flow predictable.

### 2. Signal formula
Exact math over named feature slots (`specs/FEATURES.md` 64-slot vector
and/or leader features from `replay/cross_asset_assembly.py`):

```
signal = tanh(2.0 * (leader_imbalance("ES") - primary_imbalance))
```

- Feature slots consumed (by name), including leader symbols required.
- Output range and sign convention (positive = BUY).
- Regime/event gating conditions, if any.

### 3. Falsifiable prediction
The testable claim, with horizon and threshold:

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle,  H = <horizon>, s = <threshold>
```

State what observation REFUTES the hypothesis (e.g. conditional expectancy
indistinguishable from zero across >= 4 independent events).

### 4. Cost hurdle
`hurdle = 2 x per-side all-in fee / contract_multiplier + expected slippage`
using the authoritative numbers in
`packages/backtest_pipeline/src/instrument_specs.py` and
`packages/backtest_pipeline/src/fee_model.py`. A hypothesis whose predicted
edge is below its instrument's hurdle is rejected at intake, not backtested.
Micros vs e-minis differ ~5x per side — the spec must name its instrument.

### 5. Classification and instrument binding
- Class: offensive (flow-following) / defensive (fade, trap, veto) / hybrid.
- Traded symbol(s) (`target_instrument_universe`) vs leader/clue symbols
  (`REQUIRED_LEADERS_BY_MODEL` in `replay/cross_asset_assembly.py`).
- `max_round_trips` intent: single-shot event trade or re-entering scalper.

## Wiring

The registry entry for a new hypothesis references its spec via
`hypothesis_spec_ref: docs/hypotheses/<SLUG>.md`. Intake reviews reject new
registry entries without one.
