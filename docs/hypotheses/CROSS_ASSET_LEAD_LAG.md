# CROSS_ASSET_LEAD_LAG — hypothesis spec

status: draft-complete
slug: CROSS_ASSET_LEAD_LAG
kind: pdf_structural | legacy: PDF_MODEL_2
class: `CrossAssetLeadLagModel` (packages/features_engine/src/structural_models/model_02_cross_asset_lead_lag.py:54)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Cross-Asset Lead-Lag (ridge cross-impact)

## 1. Market mechanism
Leader-contract order flow (e.g. ES OFI) carries information about lagged target returns (e.g. MES) that cross-market arbitrageurs do not fully transmit at flow level; a ridge regression of target returns on own and leader OFI estimates the cross-impact coefficient. As context it scores when leader flow predicts a lagged target move.

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.
Relationship (NOT a degeneracy): the five hypothesis-kind lead-lag slugs (ES_MES_LEAD_LAG, NQ_MNQ_LEAD_LAG, ES_NQ_DIVERGENCE_SNAPBACK, ZN_ZB_ES_NQ_MACRO_IMPULSE, MICRO_CONTRACT_RETAIL_LAG) trade tanh transforms of aggressor-imbalance divergence; this model estimates a ridge cross-impact on OFI. Same economic mechanism, different estimator and different (diagnostic) surface.

## 2. Signal formula
```
ridge fit: r_{t+1} = alpha + beta*OFI_own + gamma*OFI_leader
coef = (X'X + a*diag(0,1,1))^{-1} X'y   (intercept unpenalized)   # model_02:13-36
predicted_target_return = alpha + beta*OFI_own + gamma*OFI_leader # :39-46
lead_lag_stability = |gamma| / (|beta| + 1e-9)                    # :103
signal_decay_curve = [gamma * 0.5^k, k=0..4]                      # :49-51
```
- Inputs: OFI_smooth per asset from BOOK_PRESSURE payloads (`book_pressure_by_asset`, :88-93).
- Payload: CrossAssetLeadLagOutput(leader, target, cross_impact_score=gamma, predicted_target_return, lead_lag_stability, signal_decay_curve).

## 3. Falsifiable prediction
Feature-level (diagnostic) claim: calibrated gamma > 0 implies leader OFI positively predicts
next-step target return. Not in HORIZON_MAP_PREREGISTERED.json; H and s must be added
mechanically before testing. REFUTED if out-of-sample predicted_target_return has no positive
correlation with realized target returns on Confirmation years (2021-2022) at BH-corrected
q=0.10 (errors two-way clustered by event x calendar month).

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

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: context/diagnostic (catalog role: alpha; blocks_trade: False) — contract routes pdf_structural non-defensive payloads to context_feature
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
