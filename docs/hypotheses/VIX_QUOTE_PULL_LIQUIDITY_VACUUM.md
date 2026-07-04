# VIX_QUOTE_PULL_LIQUIDITY_VACUUM — hypothesis spec

status: draft-complete
slug: VIX_QUOTE_PULL_LIQUIDITY_VACUUM
kind: hypothesis | hyp_id: 47 | legacy: HYP_47
class: `VixQuotePullLiquidityVacuum` (packages/features_engine/src/hypotheses/vix_modules.py:55)
execution_role: sensor_conditioned_primary_alpha | standalone_hbt_policy: requires_sensor_tape
display: VIX quote-pull liquidity vacuum

## 1. Market mechanism
Decelerating VIX option quote arrivals plus stressed VIX option spreads mean options market makers are pulling quotes and withdrawing hedges; the underlying futures book thins into a liquidity vacuum, and price continues in the direction the (thin) book already leans. We ride the vacuum continuation, paid by whoever must cross the thinned book. (Grounding cited in-module: options-MM hedging->liquidity; Hasbrouck-Saar 2013 quote pulls — vix_modules.py:62-64.)

Sensor contract: requires the VIX sensor tape (required_sensors=['VIX'] via replay.cross_asset_assembly); the module abstains (returns 0) when the VIX leg is absent — VIX coverage is 2023+ only, never fabricated (vix_modules.py:70-72).

## 2. Signal formula
```
vix = cross_asset_features['VIX']; abstain if absent/NaN        # vix_modules.py:70-79
if vix_quote_arrival_accel >= 0: return 0                       # :81-82
if vix_opt_spread_stress <= 1.5: return 0                       # :83-84
if liquidity_vacuum_score <= 0.3: return 0                      # :86-88
signal = sign(book_slope) * min(1, vix_opt_spread_stress/3) * tanh(3*liquidity_vacuum_score)  # :90-92
```
- Sensor slots: `vix_quote_arrival_accel`, `vix_opt_spread_stress` (VIX leg); primary slots: `liquidity_vacuum_score`, `book_slope`.
- Range (-1,1); positive = BUY (book leans bid-side into the vacuum).

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim: VIX quote-pull (decelerating arrivals + stressed option spreads) coinciding with an underlying liquidity vacuum predicts continuation in the book-slope direction over H. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Class: offensive, sensor-conditioned (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: VIX
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran standalone: requires the VIX sensor tape (requires_sensor_tape policy); sensor legs were absent in Pass A. No economic evidence exists.
