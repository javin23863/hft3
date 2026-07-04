# VWAP_DEFENSE_BREAK — hypothesis spec

status: draft-complete
slug: VWAP_DEFENSE_BREAK
kind: hypothesis | hyp_id: 24 | legacy: HYP_24
class: `VWAPDefenseBreak` (packages/features_engine/src/hypotheses/modules.py:636)
execution_role: primary_alpha
display: VWAP defense/break

## 1. Market mechanism
Institutional execution algos benchmarked to VWAP defend the session VWAP with resting size (their benchmark constraint makes their behavior at VWAP predictable); aggressors testing VWAP without breaking the defense are faded by the algo's reloads. We trade the defense holding: near VWAP, when reload activity opposes aggressor flow, price bounces off the benchmark.

## 2. Signal formula
```
vwap_proximity   = exp(-dist_to_vwap^2 / (2*8^2))   # signed ticks, sigma=8
defense_strength = -sign(agg_imb) * iceberg_reload_score
signal = vwap_proximity * tanh(2*defense_strength)              # modules.py:651-662
```
- Slots: `distance_to_vwap` (signed ticks), `iceberg_reload_score`, `aggressor_volume_imbalance`.
- Range (-1,1); positive = BUY (defense absorbing sell flow near VWAP).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: within sigma=8 ticks of session VWAP, reload-backed defense against aggressor flow predicts reversion off VWAP over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
| ZB | 1.07 | 1000 | 0.0021 | 0.069 | 1.069 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'YM', 'MYM', 'RTY', 'M2K', 'CL', 'MCL', 'NG', 'GC', 'MGC', 'SI', 'HG', 'ZN', 'ZB', 'ZF', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=8936, net=-13338.55, realized=-14306.85, win_rate_filled=0.3145. Old-semantics/v1-expression evidence — NOT model-worth evidence.
