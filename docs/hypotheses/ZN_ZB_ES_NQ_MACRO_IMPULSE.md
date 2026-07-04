# ZN_ZB_ES_NQ_MACRO_IMPULSE — hypothesis spec

status: draft-complete
slug: ZN_ZB_ES_NQ_MACRO_IMPULSE
kind: hypothesis | hyp_id: 19 | legacy: HYP_19
class: `ZnZbToEsNqMacroImpulse` (packages/features_engine/src/hypotheses/modules.py:523)
execution_role: cross_asset_primary_alpha
display: ZN/ZB -> ES/NQ macro impulse

## 1. Market mechanism
Macro repricing hits rates futures (ZN) first — the rates complex is where macro information is natively expressed; equity index flow follows as cross-asset macro funds re-hedge equity duration exposure. Their rebalancing lag is our window. We trade equities in the direction implied by rates flow, paid by the slower macro re-hedgers.

## 2. Signal formula
```
if 'ZN' not in cross_asset_features: return 0
impulse = zn_aggressor_volume_imbalance - es_aggressor_volume_imbalance
signal = tanh(2*impulse)                                        # modules.py:530-541
```
- Leader leg: ZN `aggressor_volume_imbalance` ONLY — despite the model name, ZB is NOT consumed by the implementation (matches REQUIRED_LEADERS_BY_MODEL = ('ZN',); the name/leader audit is a PR-2/plan Phase-4 item).
- Primary: row symbol (ES/MES/NQ/MNQ per target universe). Range (-1,1); positive = BUY equities.
- Never ran in Pass A; requires ZN leader units.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: ZN-minus-equity flow divergence predicts equity mid moving toward the rates-implied direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: ['ES', 'MES', 'NQ', 'MNQ']
- Valid universe: ['ZN', 'ZB', 'ES', 'MES', 'NQ', 'MNQ']
- Required leaders: ZN | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran (lane blocked: leader tapes missing). No economic evidence exists.
