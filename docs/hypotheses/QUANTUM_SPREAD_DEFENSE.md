# QUANTUM_SPREAD_DEFENSE — hypothesis spec

status: draft-complete
slug: QUANTUM_SPREAD_DEFENSE
kind: pdf_structural | legacy: PDF_MODEL_9
class: `QuantumSpreadDefenseModel` (packages/features_engine/src/structural_models/model_09_quantum_spread.py:58)
execution_role: defensive_overlay | standalone_hbt_policy: composition_only
display: Quantum Spread Defense

## 1. Market mechanism
Models the bid-ask spread as a probability distribution over 'eigenstates' driven by two liquidity components (xi1, kappa1); when probability mass concentrates in the small-spread state relative to the wide state, the spread is primed to collapse and resting quotes are about to be run over. The overlay cancels resting quotes when collapse risk spikes (catalog description) — a pure protection veto with `blocks_trade: true`.

Why it never trades standalone: catalog role defensive + `blocks_trade: true`
(apps/workbench/config/model_catalog.yaml:70-77) derive execution_role=defensive_overlay,
standalone_hbt_policy=composition_only (model_execution_contracts.py:139-157). Quote-cancel
protection has no standalone PnL by construction.

## 2. Signal formula
```
a = (1/xi1^2 + 1/kappa1^2)/4 ; b = (1/xi1^2 - 1/kappa1^2)/4     # model_09:28-36
P(Delta) = (Delta/(xi1*kappa1)) * exp(-a*Delta^2) * I0(b*Delta^2)   # :39-49
collapse_risk = P(small_delta) / (P(small_delta) + P(wide_delta))   # :52-55
cancel_all_quotes = collapse_risk >= 0.65                       # :76-77
```
- Payload: QuantumSpreadOutput(spread_probability, collapse_risk, cancel_all_quotes, xi1, kappa1). Defaults: small_delta 0.25, wide_delta 2.0 ticks (:65-66).

## 3. Falsifiable prediction
Composed falsifiable claim: cancel_all_quotes=True windows have worse resting-quote markouts
than baseline (i.e., cancelling saves money). Not in HORIZON_MAP_PREREGISTERED.json; H must be
added mechanically before testing. REFUTED if resting-order markouts inside flagged windows
are not worse than baseline on Confirmation years (2021-2022) at BH-corrected q=0.10.

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| M2K | 0.52 | 5 | 0.2080 | 2.080 | 3.080 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| MYM | 0.52 | 0.5 | 2.0800 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| RTY | 1.52 | 50 | 0.0608 | 0.608 | 1.608 |
| YM | 1.52 | 5 | 0.6080 | 0.608 | 1.608 |
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZF | 1.07 | 1000 | 0.0021 | 0.274 | 1.274 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |
| ZT | 1.07 | 2000 | 0.0011 | 0.274 | 1.274 |

Excluded from this model's universe (removed 2026-07-04): CL, MCL, NG, GC, MGC, SI, HG — no authoritative instrument_specs/fee rows (fail-closed per PR #57) and no lake data in this program.

This slug never places standalone orders; the fee/slippage hurdle above is charged to the
HOST strategy's orders whenever this overlay gates, skews, or re-quotes them. Any composed
claim must clear the host symbol's total hurdle net of the overlay's effect (template section 4).

## 5. Classification and instrument binding
- Class: defensive veto (catalog role: defensive; blocks_trade: True)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (composition_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
