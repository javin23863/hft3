# TRANSFER_ENTROPY — hypothesis spec

status: draft-complete
slug: TRANSFER_ENTROPY
kind: pdf_structural | legacy: PDF_MODEL_8
class: `TransferEntropyModel` (packages/features_engine/src/structural_models/model_08_transfer_entropy.py:130)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Transfer Entropy Lead-Lag

## 1. Market mechanism
Transfer entropy measures directional, model-free information flow between correlated assets: TE_{X->Y} > 0 means leader X's past reduces uncertainty about target Y beyond Y's own past. When TE spikes above its control limit, the leader is genuinely driving the target and lead-lag structure is exploitable; below it, apparent lead-lag is noise. Diagnostic context that scores WHEN cross-asset conditioning is trustworthy.

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.

## 2. Signal formula
```
TE_{X->Y}(k) = H(Y_t | Y_{t-k}) - H(Y_t | X_{t-k}, Y_{t-k})     # model_08:89-110
  (discrete histogram estimator, 16 bins; entropies :14-86)
UCL = mean(TE_history) + 2*std(TE_history)  (window 50)         # :157-163
aggressive_liquidity_signal = TE > UCL and TE > 0               # :164
```
- Payload: TransferEntropyOutput(leader, target, lag, transfer_entropy, te_upper_control_limit, aggressive_liquidity_signal).
- TE is non-negative and direction-free in PRICE terms — it says the leader is informative, not which way; direction must come from the leader's own flow features.

## 3. Falsifiable prediction
Feature-level (diagnostic) claim: conditional on TE > UCL, leader-flow-conditioned target
predictions (e.g. the lead-lag family's signals) have higher IC than when TE <= UCL. Not in
HORIZON_MAP_PREREGISTERED.json; H must be added mechanically before testing. REFUTED if the
IC uplift under TE > UCL is indistinguishable from zero on Confirmation years (2021-2022) at
BH-corrected q=0.10.

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
