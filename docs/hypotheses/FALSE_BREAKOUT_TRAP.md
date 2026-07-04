# FALSE_BREAKOUT_TRAP — hypothesis spec

status: draft-complete
slug: FALSE_BREAKOUT_TRAP
kind: hypothesis | hyp_id: 8 | legacy: HYP_8
class: `FalseBreakoutTrap` (packages/features_engine/src/hypotheses/modules.py:307)
execution_role: primary_alpha
display: False breakout trap

## 1. Market mechanism
Breakout chasers buy the break of a level; when the spread is stressed but neither flow nor book slope confirms, the break was noise or a stop-hunt, and the chasers are trapped with stops just inside the level. Their constraint: tight stops behind a fake break force them to sell back. We fade unconfirmed breaks and are paid by trapped chasers' exits.

## 2. Signal formula
```
confirmation = (|tanh(2*agg_imb)| + |tanh(2*book_slope)|) / 2
signal = clip(-spread_stress_elevated * (1 - confirmation) * tanh(2*agg_imb), -1, 1)
                                                                # modules.py:318-333
```
- Slots: `spread_stress_elevated` (binary, spread_stress>2), `aggressor_volume_imbalance`, `book_slope`.
- Range [-1,1]; positive = BUY. Fires only when spread is in stressed regime AND confirmation is low.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 3000 ms, s = 0.05
```

Directional claim: stressed-spread moves without flow/slope confirmation revert over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| M2K | 0.52 | 5 | 0.2080 | 2.080 | 3.080 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| RTY | 1.52 | 50 | 0.0608 | 0.608 | 1.608 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'RTY', 'M2K']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=1774, net=-706.53, realized=-696.33, win_rate_filled=0.1161. Old-semantics/v1-expression evidence — NOT model-worth evidence.
