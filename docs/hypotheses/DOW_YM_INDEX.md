# DOW_YM_INDEX — hypothesis spec

status: draft-complete
slug: DOW_YM_INDEX
kind: pdf_structural | legacy: PDF_MODEL_6
class: `DowYMIndexModel` (packages/features_engine/src/structural_models/model_06_dow_ym_index.py:28)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Dow/YM Price-Weighted Index

## 1. Market mechanism
The Dow is price-weighted (Index = sum of prices / divisor), so OFI in the few highest-priced constituents mechanically moves YM fair value more than equal-dollar flow elsewhere; a weighted top-constituent OFI is a synthetic index-pressure signal for YM. Diagnostic context for YM/MYM fair-value drift.

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.
DATA DEPENDENCY (flagged): requires constituent equity prices and per-constituent OFI (dow_constituents.yaml fixture / `book_pressure_by_asset` for stock symbols) — constituent equity feeds are NOT part of this program's CME futures lake, so live payloads cannot currently be produced from lake data.

## 2. Signal formula
```
index_level = sum(P_i) / divisor                                # model_06:14-18
w_i = P_i / sum(P)                                              # :21-25
synthetic_Dow_pressure = sum(w_i * OFI_i, top-5 by price) / sum(w_i)   # :58-70
constituent_to_YM_signal = pressure * index_level/(index_level+1e-9)   # :71  (~= pressure)
```
- Payload: DowYMIndexOutput(component_price_weight, top_component_OFI, synthetic_Dow_pressure, YM_fair_pressure, constituent_to_YM_signal).

## 3. Falsifiable prediction
Feature-level (diagnostic) claim: synthetic_Dow_pressure > 0 predicts positive YM mid drift
over short horizons. Untestable in this program until constituent feeds exist (see data
dependency above); not in HORIZON_MAP_PREREGISTERED.json. REFUTED if, with constituent data,
the conditional expectancy is indistinguishable from zero on Confirmation years at
BH-corrected q=0.10.

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| MYM | 0.52 | 0.5 | 2.0800 | 2.080 | 3.080 |
| YM | 1.52 | 5 | 0.6080 | 0.608 | 1.608 |

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: context/diagnostic (catalog role: alpha; blocks_trade: False) — contract routes pdf_structural non-defensive payloads to context_feature
- Target universe: (none declared — no target constraint)
- Valid universe: ['MYM', 'YM']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
