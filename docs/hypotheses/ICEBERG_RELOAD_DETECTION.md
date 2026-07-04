# ICEBERG_RELOAD_DETECTION — hypothesis spec

status: draft-complete
slug: ICEBERG_RELOAD_DETECTION
kind: hypothesis | hyp_id: 13 | legacy: HYP_13
class: `IcebergReloadDetection` (packages/features_engine/src/hypotheses/modules.py:350)
execution_role: primary_alpha
display: Iceberg/reload detection

## 1. Market mechanism
An iceberg order reloading at a level is an institution with a large parent order defending a price — its constraint is benchmark/completion pressure (it must fill size near this level). Joining the reloading side rides institutional intent; the counterparty is flow trying to break a level that keeps refilling. We are paid by the defended level holding and price moving off it.

## 2. Signal formula
```
signal = tanh(2*iceberg_reload_score)                           # modules.py:357-360
```
- Slot: `iceberg_reload_score` (signed by reloading side).
- Range (-1,1); positive = BUY (bid reloading). Single-feature transform.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: detected reload activity predicts mid moving away from the defended level in the reload direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Pass A (expression v1, flattened semantics): rows=9131, net=-20661.52, realized=-17220.73, win_rate_filled=0.3159. Old-semantics/v1-expression evidence — NOT model-worth evidence.
