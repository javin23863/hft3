# SECOND_WAVE_CONTINUATION — hypothesis spec

status: draft-complete
slug: SECOND_WAVE_CONTINUATION
kind: hypothesis | hyp_id: 1 | legacy: HYP_1
class: `SecondWaveContinuation` (packages/features_engine/src/hypotheses/modules.py:151)
execution_role: primary_alpha
display: Second-wave continuation

## 1. Market mechanism
After the initial event impulse, momentum traders and slower systematic accounts who missed the first leg chase the move, while stopped-out holders of the opposite side liquidate into it. Their constraint is arrival lag: they must pay up to participate after the information is public, producing a second wave of one-sided aggressor flow in the impulse direction. We are paid by late chasers' marketable flow continuing to push price after our earlier entry on the same side.

## 2. Signal formula
```
signal = tanh(3.0 * aggressor_volume_imbalance)          # modules.py:158-162
```
- Slots: `aggressor_volume_imbalance` (slot 0) only.
- Output range [-1, 1]; positive = BUY (follow the aggressor flow).
- No regime/event gating.
- NOTE: single-feature momentum transform; mathematically identical in form to PASSIVE_TRAP_FILL (tanh(3*agg_imb)) — the two hypotheses are currently the same signal with different names.
- DEGENERACY (PR-0b cross-flag): TRAILING_DRAWDOWN_PRESSURE (modules.py:770-781) is this identical formula tanh(3*agg_imb) restricted to the trend_continuation regime — a regime-slice of this slug, not an independent mechanism. Never count the three (SECOND_WAVE_CONTINUATION, PASSIVE_TRAP_FILL, TRAILING_DRAWDOWN_PRESSURE) as independent trials.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 5000 ms, s = 0.08
```

Directional claim: strong positive aggressor imbalance predicts further upward mid drift over the next 5s (and symmetrically down). REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Pass A (expression v1, flattened semantics): rows=10293, net=-22955.16, realized=-21825.41, win_rate_filled=0.2396. Old-semantics/v1-expression evidence — NOT model-worth evidence.
