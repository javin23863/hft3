# OPENING_CANDLE_CHASE — hypothesis spec

status: draft-complete
slug: OPENING_CANDLE_CHASE
kind: hypothesis | hyp_id: 23 | legacy: HYP_23
class: `OpeningCandleChase` (packages/features_engine/src/hypotheses/modules.py:619)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Opening candle chase

## 1. Market mechanism
At the cash-equity open, retail and momentum accounts chase the opening drive with market orders while spreads are still stressed, paying the spread tax at the worst moment. When the chase happens into a stressed spread, the move tends to retrace once the chasers are filled. We fade the chased direction, paid by chasers' aggression into thin, expensive books.

## 2. Signal formula
```
if event_context != 'CASH_EQUITY_OPEN': return 0
stress_act = 1 - exp(-max(0, spread_stress - 1))
signal = -stress_act * tanh(2*aggressor_volume_imbalance)       # modules.py:627-634
```
- Slots: `aggressor_volume_imbalance`, `spread_stress`; gated to CASH_EQUITY_OPEN windows ([-60s,+10s] rule-based, packages/data_system/config/events.csv).
- Range (-1,1); positive = BUY (fading sell-side chase into stress).

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim: aggressor chase into a stressed spread at the cash open predicts retracement against the chase over H. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Class: defensive-fade (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Not in the PR-0a active set (27 ran + 5 lead-lag). No standalone economics on record for this slug under honest semantics (campaign hbt_stagec3_a326db8f). No model-worth evidence exists.
