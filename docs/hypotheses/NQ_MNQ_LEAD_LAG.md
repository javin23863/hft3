# NQ_MNQ_LEAD_LAG — hypothesis spec

status: draft-complete
slug: NQ_MNQ_LEAD_LAG
kind: hypothesis | hyp_id: 17 | legacy: HYP_17
class: `NqToMnqLeadLag` (packages/features_engine/src/hypotheses/modules.py:482)
execution_role: cross_asset_primary_alpha
display: NQ -> MNQ lead-lag

## 1. Market mechanism
Same mechanism as ES->MES in the Nasdaq complex: NQ flow is institutional, MNQ is the retail micro cohort reacting late. The lagging cohort's marketable catch-up flow pays us. MNQ's fee hurdle is 2.08 ticks fees-only — the highest in the active book — so the edge must be materially larger than for MES.

## 2. Signal formula
```
if 'NQ' not in cross_asset_features: return 0
divergence = nq_aggressor_volume_imbalance - mnq_aggressor_volume_imbalance
signal = tanh(2*divergence)                                     # modules.py:489-499
```
- Leader leg: NQ `aggressor_volume_imbalance`; primary: MNQ. Range (-1,1); positive = BUY MNQ.
- Never ran in Pass A; requires NQ leader units (PR-2; NQ tape coverage gap to be itemized).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: NQ-minus-MNQ flow divergence predicts MNQ mid moving toward the NQ flow direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: ['MNQ']
- Valid universe: ['NQ', 'MNQ']
- Required leaders: NQ | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran (lane blocked: leader tapes missing). No economic evidence exists.
