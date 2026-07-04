# SPREAD_BLOWOUT_RECOMPRESSION — hypothesis spec

status: draft-complete
slug: SPREAD_BLOWOUT_RECOMPRESSION
kind: hypothesis | hyp_id: 5 | legacy: HYP_5
class: `SpreadBlowoutRecompression` (packages/features_engine/src/hypotheses/modules.py:135)
execution_role: primary_alpha
display: Spread blowout/recompression

## 1. Market mechanism
During a spread blowout, quote machines have pulled to their widest risk limits; the first side to re-quote reveals where inventory-constrained MMs see value. Late aggressors still paying the blown-out spread are the tax donors. We trade in the direction of the rebuilding book slope during recompression and are paid by spreads normalizing around the new level.

## 2. Signal formula
```
activation = 1 - exp(-max(0, spread_stress - 1.0))
signal = activation * tanh(2*book_slope)                        # modules.py:142-149
```
- Slots: `spread_stress` (baseline 1.0), `book_slope`.
- Range approx (-1,1); positive = BUY. Active only when spread stress exceeds baseline.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 1000 ms, s = 0.02
```

Directional claim: during elevated spread stress, the sign of the book slope predicts mid drift over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| MYM | 0.52 | 0.5 | 2.0800 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| YM | 1.52 | 5 | 0.6080 | 0.608 | 1.608 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'YM', 'MYM']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=7419, net=-2764.57, realized=-2764.57, win_rate_filled=0.1111. Old-semantics/v1-expression evidence — NOT model-worth evidence.
