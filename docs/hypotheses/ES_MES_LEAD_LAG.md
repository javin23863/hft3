# ES_MES_LEAD_LAG — hypothesis spec

status: draft-complete
slug: ES_MES_LEAD_LAG
kind: hypothesis | hyp_id: 16 | legacy: HYP_16
class: `EsToMesLeadLag` (packages/features_engine/src/hypotheses/modules.py:461)
execution_role: cross_asset_primary_alpha
display: ES -> MES lead-lag

## 1. Market mechanism
ES order flow is institutionally dominated; MES flow is retail/prop dominated and reacts with a lag (attention + platform latency + smaller-size cohort constraints). Cross-market arbitrageurs enforce price linkage in milliseconds but the FLOW imbalance linkage lags — MES aggressor flow catches up to ES flow. We trade MES in the direction ES flow has already moved, paid by the lagging cohort's catch-up flow.

## 2. Signal formula
```
if 'ES' not in cross_asset_features: return 0    # fail-silent without leader
divergence = es_aggressor_volume_imbalance - mes_aggressor_volume_imbalance
signal = tanh(2*divergence)                                     # modules.py:468-480
```
- Leader leg: ES `aggressor_volume_imbalance` (PIT-aligned via enrich_cross_leg, staleness cap 5s).
- Primary: MES `aggressor_volume_imbalance`. Range (-1,1); positive = BUY MES (ES flow more bullish than MES).
- Never ran in Pass A (leader tapes absent) — first test comes with PR-2 unlock.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: ES-minus-MES flow divergence predicts MES mid moving toward the ES flow direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: ['MES']
- Valid universe: ['ES', 'MES']
- Required leaders: ES | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran (lane blocked: leader tapes missing). No economic evidence exists.
