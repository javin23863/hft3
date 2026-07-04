# VIX_DEPTH_IMBALANCE_DIRECTION — hypothesis spec

status: draft-complete
slug: VIX_DEPTH_IMBALANCE_DIRECTION
kind: hypothesis | hyp_id: 49 | legacy: HYP_49
class: `VixDepthImbalanceDirection` (packages/features_engine/src/hypotheses/vix_modules.py:135)
execution_role: sensor_conditioned_primary_alpha
display: VIX depth imbalance direction

## 1. Market mechanism
Bid-heavy depth in VIX options is dealers positioning for downside-hedge demand — institutions buying crash protection. Their hedging constraint telegraphs equity-selling pressure before it fully expresses in the underlying. We short the underlying when VIX-option depth skews bid-heavy during event windows, paid by the hedgers' subsequent delta flow.

## 2. Signal formula
```
gates: VIX leg; depth_imb finite; event ctx _TIGHT/macro
damping = 1/(1 + max(0, spread_stress - 1))
signal  = -tanh(2*vix_opt_depth_imbalance) * damping    # vix_modules.py:149-166
```
- Sensor: `vix_opt_depth_imbalance`; slot: `spread_stress`.
- Range [-1,1]; positive = BUY (ask-heavy VIX depth = downside-hedge unwind). Damped when spread already stressed.
- Pass A: best win-rate of the book (0.36) but realized -$402 under v1 expression — the model to watch under v2.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: bid-heavy VIX-option depth during event windows predicts downward underlying mid drift over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Required leaders: none | Required sensors: VIX
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=592, net=976.28, realized=-402.06, win_rate_filled=0.363. Old-semantics/v1-expression evidence — NOT model-worth evidence.
