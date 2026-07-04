# CUTOFF_PANIC_EXITS — hypothesis spec

status: draft-complete
slug: CUTOFF_PANIC_EXITS
kind: hypothesis | hyp_id: 30 | legacy: HYP_30
class: `CutoffPanicExits` (packages/features_engine/src/hypotheses/modules.py:692)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Cutoff panic exits

## 1. Market mechanism
In the minutes before a prop-firm cutoff (Topstep 15:10 CT daily flatten; the Friday 16:00 ET close where all prop firms must be flat), traders at or near their daily loss limit flatten rapidly regardless of price — rule-driven, information-free one-sided flow. The cutoff_pressure_score slot measures that forced-exit pressure; we ride the exit direction, paid by the mechanical urgency of rule-bound flatteners. (PC4 note in the class docstring: the original TPT_FLATTEN/APEX_FLATTEN gates were never produced by the context engine and were repointed to contexts that exist — modules.py:700-711.)

## 2. Signal formula
```
if event_context not in ('PROP_FLATTEN_TOPSTEP', 'FRIDAY_CLOSE'): return 0
signal = tanh(3*cutoff_pressure_score)                          # modules.py:717-720
```
- Slot: `cutoff_pressure_score` (feature_index.py:93); gated to prop-cutoff event contexts.
- Range (-1,1); positive = BUY (forced buying pressure).
- DEGENERACY WARNING: DAILY_LOSS_LIMIT_DEFENSE reads the SAME slot with the OPPOSITE sign (-tanh(2*cutoff), modules.py:763-768) under a different gate — the two slugs encode contradictory directional claims on one measurement; at most one can be right per regime. Flagged in both specs.

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim: measured forced-exit pressure inside prop-cutoff windows predicts continuation in the exit direction over H. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
