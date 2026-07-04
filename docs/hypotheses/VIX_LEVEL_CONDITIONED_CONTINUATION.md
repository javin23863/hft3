# VIX_LEVEL_CONDITIONED_CONTINUATION — hypothesis spec

status: draft-complete
slug: VIX_LEVEL_CONDITIONED_CONTINUATION
kind: hypothesis | hyp_id: 50 | legacy: HYP_50
class: `VixLevelConditionedContinuation` (packages/features_engine/src/hypotheses/vix_modules.py:169)
execution_role: sensor_conditioned_primary_alpha
display: VIX level-conditioned continuation

## 1. Market mechanism
In elevated-vol regimes (VIX ATM strike >= 20) with an event-shock posterior, momentum continuation is stronger and reversion weaker — high-vol regimes lengthen the reaction chain of late responders. Identical trade expression to SECOND_WAVE but conditioned on the vol regime, so the counterparty is the same late-chaser cohort, filtered to regimes where their behavior persists.

## 2. Signal formula
```
gates: VIX leg; vix_atm_strike >= 20; regime_posterior['event_shock'] > 0
signal = tanh(3*agg_imb)                                # vix_modules.py:188-206
```
- Sensor: `vix_atm_strike`; slot: `aggressor_volume_imbalance`; regime posterior gate.
- Range [-1,1]; positive = BUY. Same core transform as HYP_1, regime-gated.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: in VIX>=20 event-shock regimes, aggressor imbalance predicts continuation over 15s, with higher conditional expectancy than the ungated HYP_1 (that comparison is part of the test). REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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

Symbols in the declared universe WITHOUT an authoritative spec/fee row (CL, GC, HG, MCL, MGC, NG, SI) are fail-closed: NOT tradable and rejected at intake until instrument_specs.py/fee_model.py gain their rows (no lake data exists for them in this program either).

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'YM', 'MYM', 'RTY', 'M2K', 'CL', 'MCL', 'NG', 'GC', 'MGC', 'SI', 'HG', 'ZN', 'ZB', 'ZF', 'ZT']
- Required leaders: none | Required sensors: VIX
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=263, net=1.33, realized=-111.63, win_rate_filled=0.1535. Old-semantics/v1-expression evidence — NOT model-worth evidence.
