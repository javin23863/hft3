# PRIOR_HIGH_LOW_BREAKOUT_TRAP — hypothesis spec

status: draft-complete
slug: PRIOR_HIGH_LOW_BREAKOUT_TRAP
kind: hypothesis | hyp_id: 22 | legacy: HYP_22
class: `PriorHighLowBreakoutTrap` (packages/features_engine/src/hypotheses/modules.py:417)
execution_role: primary_alpha
display: Prior high/low breakout trap

## 1. Market mechanism
Session-extreme breaks trigger both breakout entries and resting stops; when the break lacks aggressor confirmation it was a stop-sweep, and breakout traders are trapped outside the range. Their constraint: stops placed just back inside the range guarantee forced exits on re-entry. We fade unconfirmed session-extreme breaks, paid by the trapped cohort's unwinds.

## 2. Signal formula
```
trap   = sign(is_breaking_session_level) * (|is_breaking_session_level| - |tanh(2*agg_imb)|)
signal = -tanh(2*trap)                                          # modules.py:429-436
```
- Slots: `is_breaking_session_level` (+1 high break / -1 low break / 0), `aggressor_volume_imbalance`.
- Range (-1,1); positive = BUY (fading an unconfirmed low break).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: session-extreme breaks without proportional aggressor flow revert back into the range over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZF | 1.07 | 1000 | 0.0021 | 0.274 | 1.274 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |
| ZT | 1.07 | 2000 | 0.0011 | 0.274 | 1.274 |

Excluded from this model's universe (removed 2026-07-04): CL, MCL, NG, GC, MGC, SI, HG — no authoritative instrument_specs/fee rows (fail-closed per PR #57) and no lake data in this program.

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'YM', 'MYM', 'RTY', 'M2K', 'ZN', 'ZB', 'ZF', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=480, net=-2550.78, realized=-556.38, win_rate_filled=0.1742. Old-semantics/v1-expression evidence — NOT model-worth evidence.
