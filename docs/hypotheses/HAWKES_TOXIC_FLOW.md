# HAWKES_TOXIC_FLOW — hypothesis spec

status: draft-complete
slug: HAWKES_TOXIC_FLOW
kind: pdf_structural | legacy: PDF_MODEL_11
class: `HawkesToxicFlowModel` (packages/features_engine/src/structural_models/model_11_hawkes_toxic.py:66)
execution_role: defensive_overlay | standalone_hbt_policy: composition_only
display: Hawkes Toxic Flow Detection

## 1. Market mechanism
Order arrivals self-excite (Hawkes): each aggressive order raises the intensity of further aggression; when intensity runs a multiple above baseline, a self-reinforcing toxic cascade is underway and passive quotes are being adversely selected. The overlay raises the risk-aversion gamma and skews the Avellaneda-Stoikov reservation price away from the cascade (catalog description; requires HYBRID_EXECUTION).

Why it never trades standalone: kind=pdf_structural with catalog role defensive derives
execution_role=defensive_overlay, standalone_hbt_policy=composition_only
(model_execution_contracts.py:139-157: defensive structural payloads gate execution). The
payload reshapes host quoting/aggression; a standalone run of a risk gate would fabricate
PnL, so the manifest records composition-only receipts (no-cherry-pick v2).
Composition consumers: HYBRID_EXECUTION (gamma scaling and reservation_price_skew feed the AS quote engine; catalog `requires: [HYBRID_EXECUTION]`, apps/workbench/config/model_catalog.yaml:85-93).

## 2. Signal formula
```
lambda(t) = mu + sum_i alpha*exp(-beta*(t - t_i))               # model_11:13-28
multivariate: lambda_j(t) = mu_j + sum_i sum alpha_{i->j} exp(-beta*(t-t_i))   # :31-52
stability guard: spectral radius(alpha/beta matrix) >= 1 -> output zeroed      # :91-106
toxic_cascade_score = max_k(lambda_k / mu_k) - 1                # :55-63
toxic if score >= 1.0 ; gamma = gamma_base * 2 when toxic       # :116-117
reservation_price_skew = -score * hybrid_reservation * 1e-4 (else -score)      # :118-122
```
- Payload: HawkesToxicOutput(intensity_by_class, toxic_cascade_score, risk_aversion_gamma, reservation_price_skew, toxic_flow_detected).

## 3. Falsifiable prediction
Composed falsifiable claim: toxic_flow_detected=True predicts continued same-side aggression
and worse passive markouts over the cascade decay window. Not in
HORIZON_MAP_PREREGISTERED.json; H must be added mechanically before testing. REFUTED if
markout deterioration conditional on the flag is indistinguishable from baseline on
Confirmation years (2021-2022) at BH-corrected q=0.10.

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
- Class: defensive (catalog role: defensive; blocks_trade: False; requires: HYBRID_EXECUTION)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (composition_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
