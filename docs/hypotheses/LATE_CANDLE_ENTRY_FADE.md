# LATE_CANDLE_ENTRY_FADE — hypothesis spec

status: draft-complete
slug: LATE_CANDLE_ENTRY_FADE
kind: hypothesis | hyp_id: 26 | legacy: HYP_26
class: `LateCandleEntryFade` (packages/features_engine/src/hypotheses/modules.py:198)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Late candle entry fade

## 1. Market mechanism
Retail traders enter late into an established candle/trend move; their late entries are weak hands positioned at the worst prices, and their stops feed the reversal. The intended trade is to fade late-entry weakness inside a trend-continuation regime.

## 2. Signal formula
```
if regime_state != 'trend_continuation': return 0
signal = clip(exp(-5*|aggressor_volume_imbalance|) - 0.5, -1, 1)   # modules.py:209-212
```
- Slot: `aggressor_volume_imbalance` (magnitude only); regime gate `trend_continuation` (regime_filter.py:79).
- Effective range (-0.5, 0.5].
- IMPLEMENTATION PATHOLOGY (flagged, not fixed here): the signal's sign encodes only the MAGNITUDE of the imbalance — positive (BUY) whenever |agg_imb| < ln(2)/5 ~= 0.139, negative (SELL) otherwise — it never encodes the trend's or the flow's direction. As implemented, the directional claim of the mechanism is not expressed; a "quiet flow in trend regime => BUY" rule has no mechanism support. Intake must treat the current formula as incoherent with section 1.

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim (as implemented, recorded for honesty): low |aggressor imbalance| inside a trend_continuation regime predicts positive mid drift over H — a claim the section-1 mechanism does NOT support. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Not in the PR-0a active set (27 ran + 5 lead-lag). No standalone economics on record for this slug under honest semantics (campaign hbt_stagec3_a326db8f). No model-worth evidence exists.
