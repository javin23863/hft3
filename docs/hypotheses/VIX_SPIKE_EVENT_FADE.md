# VIX_SPIKE_EVENT_FADE — hypothesis spec

status: draft-complete
slug: VIX_SPIKE_EVENT_FADE
kind: hypothesis | hyp_id: 46 | legacy: HYP_46
class: `VixSpikeEventFade` (packages/features_engine/src/hypotheses/vix_modules.py:10)
execution_role: sensor_conditioned_primary_alpha
display: VIX spike event fade

## 1. Market mechanism
When VIX-option activity shows a jump (bipower variance spiking over TSRV), execution-demand-driven aggressors in the underlying overshoot — they pay any price for immediacy during the vol event. Their constraint is urgency, not information. We fade large aggressor imbalance during detected vol jumps, paid by the intrabar snap-back once urgency-driven flow clears.

## 2. Signal formula
```
gates: VIX leg present; bipower/tsrv finite; event ctx _TIGHT/macro;
       jump_ratio = bipower/(tsrv+eps) > 1.5; |agg_imb| > 0.5
fade = -sign(agg_imb) * clip(jump_ratio - 1, 0, 1) * |tanh(2*agg_imb)|
signal = clip(fade, -1, 1)                              # vix_modules.py:24-52
```
- Sensor features: `vix_opt_bipower_var`, `vix_opt_tsrv` (VIX.OPT npz, PIT-synced); slot: `aggressor_volume_imbalance`.
- Range [-1,1]; positive = BUY (fading a sell overshoot). Abstains (0) without VIX coverage.
- Grounding: Barndorff-Nielsen & Shephard 2004 jump/diffusion separation.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: during VIX-detected vol jumps, extreme aggressor imbalance mean-reverts over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Required leaders: none | Required sensors: VIX
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=45, net=221.43, realized=-234.7, win_rate_filled=0.1905. Old-semantics/v1-expression evidence — NOT model-worth evidence.
