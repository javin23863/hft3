# PASSIVE_TRAP_FILL — hypothesis spec

status: draft-complete
slug: PASSIVE_TRAP_FILL
kind: hypothesis | hyp_id: 42 | legacy: HYP_42
class: `PassiveTrapFill` (packages/features_engine/src/hypotheses/modules.py:438)
execution_role: primary_alpha
display: Passive trap fill

## 1. Market mechanism
Passive limit orders resting through fast one-sided flow are filled precisely when the market runs through them — the resting trader (rebate-seeking MM or hopeful mean-reverter) is adversely selected by momentum flow. Their constraint: queue-position value makes them reluctant to cancel. The implemented signal follows the aggressor flow that does the trapping.

## 2. Signal formula
```
signal = tanh(3*aggressor_volume_imbalance)                     # modules.py:445-449
```
- Slot: `aggressor_volume_imbalance` only.
- Range (-1,1); positive = BUY.
- DEGENERATE: byte-identical math to SECOND_WAVE_CONTINUATION (tanh(3*agg_imb)). The docstring says "simulated continuous loss function representation" — this hypothesis currently duplicates HYP_1 and tests nothing distinct. Spec verdict: needs a real queue-trap formula (fill-side conditional) or retirement.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim (as implemented): identical to SECOND_WAVE_CONTINUATION; any IC difference from HYP_1 is noise by construction. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Pass A (expression v1, flattened semantics): rows=9304, net=-12986.92, realized=-7962.39, win_rate_filled=0.299. Old-semantics/v1-expression evidence — NOT model-worth evidence.
