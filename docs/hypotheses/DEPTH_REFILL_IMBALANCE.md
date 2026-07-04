# DEPTH_REFILL_IMBALANCE — hypothesis spec

status: draft-complete
slug: DEPTH_REFILL_IMBALANCE
kind: hypothesis | hyp_id: 4 | legacy: HYP_4
class: `DepthRefillImbalance` (packages/features_engine/src/hypotheses/modules.py:119)
execution_role: primary_alpha
display: Depth-refill imbalance

## 1. Market mechanism
After a level clears, liquidity providers competing for queue priority refill the winning side first (adds dominate cancels); their constraint is queue position economics — being early in the new queue is valuable exactly when they expect the level to hold. Late-reacting opposite-side liquidity pays the spread to reposition. We trade with the refill direction and are paid by the slower side's repricing.

## 2. Signal formula
```
refill_strength = exp(-cancel_to_add_ratio)
signal = tanh(5*book_slope_change) * refill_strength           # modules.py:126-133
```
- Slots: `book_slope_change`, `cancel_to_add_ratio`.
- Range approx (-1,1); positive = BUY (slope building bid-side while adds dominate).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 1000 ms, s = 0.03
```

Directional claim: rising book slope with add-dominated flow predicts upward mid drift over 1s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'ZN', 'ZB']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=11353, net=-9913.49, realized=-10063.24, win_rate_filled=0.1119. Old-semantics/v1-expression evidence — NOT model-worth evidence.
