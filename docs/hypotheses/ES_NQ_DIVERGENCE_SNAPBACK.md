# ES_NQ_DIVERGENCE_SNAPBACK — hypothesis spec

status: draft-complete
slug: ES_NQ_DIVERGENCE_SNAPBACK
kind: hypothesis | hyp_id: 18 | legacy: HYP_18
class: `EsNqDivergenceSnapback` (packages/features_engine/src/hypotheses/modules.py:501)
execution_role: cross_asset_primary_alpha
display: ES/NQ divergence snapback

## 1. Market mechanism
ES and NQ are cointegrated index exposures arbitraged by basket/stat-arb desks; when short-horizon FLOW imbalance diverges between them beyond what index composition justifies, the arbitrage cohort's re-hedging flow snaps the divergence back. We fade the divergence, paid by arbitrageurs' enforcement trades. Target policy (which leg to trade) must be fixed by spec: current campaign binding trades the row symbol.

## 2. Signal formula
```
if 'NQ' or 'ES' missing from cross_asset_features: return 0
divergence = es_aggressor_volume_imbalance - nq_aggressor_volume_imbalance
signal = -tanh(1.5*divergence)                                  # modules.py:508-521
```
- Leader legs: BOTH ES and NQ `aggressor_volume_imbalance`; primary symbol carries the trade.
- Range (-1,1); positive = BUY primary when ES flow lags NQ. Never ran in Pass A.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: extreme ES-vs-NQ flow divergence mean-reverts over 15s, moving the traded leg toward convergence. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ']
- Required leaders: ES, NQ | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran (lane blocked: leader tapes missing). No economic evidence exists.
