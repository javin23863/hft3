# STOP_RUN_EXHAUSTION_FADE — hypothesis spec

status: draft-complete
slug: STOP_RUN_EXHAUSTION_FADE
kind: hypothesis | hyp_id: 2 | legacy: HYP_2
class: `StopRunExhaustionFade` (packages/features_engine/src/hypotheses/modules.py:97)
execution_role: primary_alpha
display: Stop-run exhaustion fade

## 1. Market mechanism
Stop-loss holders clustered beyond a swept level are forced sellers/buyers whose orders execute at the sweep, exhausting one side; the sweeping aggressors (short-horizon momentum/stop-hunt flow) must exit to monetize. Their constraint: once resting stops are consumed, continuation requires fresh outside interest that is not present, so the book refills against them. We fade the sweep direction and are paid by the hunters' profit-taking and trapped late entrants' unwinds.

## 2. Signal formula
```
activation  = clip(near_touch_cancel_pressure, 0, 1) * |tanh(2*agg_imb)|
fade_dir    = -sign(agg_imb)
slope_align = clip(fade_dir * book_slope, 0, 1)
signal      = clip(activation * slope_align * fade_dir, -1, 1)   # modules.py:105-117
```
- Slots: `near_touch_cancel_pressure`, `aggressor_volume_imbalance`, `book_slope`.
- Range [-1,1]; positive = BUY (fading a sell sweep). Fires only when book slope already opposes the aggressor flow.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 3000 ms, s = 0.05
```

Directional claim: after an aggressive one-sided sweep with high near-touch cancels and an opposing book slope, mid reverts against the sweep over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Pass A (expression v1, flattened semantics): rows=4139, net=-3787.24, realized=-3561.77, win_rate_filled=0.1647. Old-semantics/v1-expression evidence — NOT model-worth evidence.
