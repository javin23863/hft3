# TREASURY_CTD — hypothesis spec

status: draft-complete
slug: TREASURY_CTD
kind: pdf_structural | legacy: PDF_MODEL_7
class: `TreasuryCTDModel` (packages/features_engine/src/structural_models/model_07_treasury_ctd.py:50)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Treasury CTD / Implied Repo

## 1. Market mechanism
Treasury futures price off the cheapest-to-deliver bond; delivery cost and implied repo across the deliverable basket determine basis richness/cheapness and the quality-option value. When the CTD is near switching, futures carry extra optionality and basis behavior changes. Diagnostic context for ZT/ZF/ZN/ZB basis and quality-option regime.

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.
DATA DEPENDENCY (flagged): requires a deliverable-basket fixture (treasury_deliverable_basket.yaml: bond prices, conversion factors) — cash-treasury quotes are NOT part of this program's CME futures lake, so live payloads cannot currently be produced from lake data.

## 2. Signal formula
```
delivery_cost = P_bond - F*CF                                   # model_07:14-20
implied_repo = (F*CF - P_bond)/P_bond * 360/days                # :23-33
CTD = argmin(delivery_cost); switch_threshold = cost_2nd - cost_1st   # :36-47
quality_option_pressure = (max_cost - min_cost)/(|min_cost|+1e-9)     # :79-84
futures_basis_signal = -delivery_cost[CTD]                      # :86-89
```
- Payload: TreasuryCTDOutput(current_CTD, delivery_cost_by_bond, implied_repo_by_bond, CTD_switch_threshold, quality_option_pressure, futures_basis_signal).

## 3. Falsifiable prediction
Feature-level (diagnostic) claim: a rich basis (futures_basis_signal > 0, futures expensive
vs CTD) predicts basis convergence (futures underperformance vs carry) into delivery.
Untestable in this program until cash-bond data exists (see data dependency above); not in
HORIZON_MAP_PREREGISTERED.json. REFUTED if, with basket data, conditional convergence is
indistinguishable from zero on Confirmation years at BH-corrected q=0.10.

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZF | 1.07 | 1000 | 0.0021 | 0.274 | 1.274 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |
| ZT | 1.07 | 2000 | 0.0011 | 0.274 | 1.274 |

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: context/diagnostic (catalog role: hybrid; blocks_trade: False) — contract routes pdf_structural non-defensive payloads to context_feature
- Target universe: (none declared — no target constraint)
- Valid universe: ['ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
