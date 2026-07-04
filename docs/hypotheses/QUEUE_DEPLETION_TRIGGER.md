# QUEUE_DEPLETION_TRIGGER — hypothesis spec

status: draft-complete
slug: QUEUE_DEPLETION_TRIGGER
kind: hypothesis | hyp_id: 10 | legacy: HYP_10
class: `QueueDepletionTrigger` (packages/features_engine/src/hypotheses/modules.py:605)
execution_role: primary_alpha
display: Queue depletion trigger

## 1. Market mechanism
When one side's queue depletes faster (fills and cancels exceed refills), the resting liquidity on that side is losing the inventory battle and the level will break. The counterparty is the passive quoter who defends a dying level too long — queue-position sunk cost delays their withdrawal. We hit the breaking direction and are paid by the level's mechanical failure.

## 2. Signal formula
```
signal = tanh(3*(queue_depletion_rate_ask - queue_depletion_rate_bid))
                                                                # modules.py:612-617
```
- Slots: `queue_depletion_rate_bid`, `queue_depletion_rate_ask`.
- Range (-1,1); positive = BUY (ask depleting faster).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 1000 ms, s = 0.03
```

Directional claim: differential queue depletion predicts mid moving toward the depleting side over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| ZB | 1.07 | 1000 | 0.0021 | 0.069 | 1.069 |
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
Pass A (expression v1, flattened semantics): rows=8249, net=-4266.74, realized=-4536.5, win_rate_filled=0.1222. Old-semantics/v1-expression evidence — NOT model-worth evidence.
