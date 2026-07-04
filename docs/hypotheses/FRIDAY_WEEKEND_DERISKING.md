# FRIDAY_WEEKEND_DERISKING — hypothesis spec

status: draft-complete
slug: FRIDAY_WEEKEND_DERISKING
kind: hypothesis | hyp_id: 37 | legacy: HYP_37
class: `FridayWeekendDerisking` (packages/features_engine/src/hypotheses/modules.py:809)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Friday/weekend de-risking

## 1. Market mechanism
Into the Friday close, accounts that may not carry weekend risk (prop rules, retail margin) de-risk mechanically; the resulting aggressor-flow imbalance is information-free and predictable in direction. We follow the de-risking flow, paid by holders forced out by calendar rules rather than price information.

## 2. Signal formula
```
if event_context != 'FRIDAY_CLOSE': return 0
signal = tanh(2*aggressor_volume_imbalance)                     # modules.py:817-820
```
- Slot: `aggressor_volume_imbalance` only; gated to FRIDAY_CLOSE (rule-based windows in packages/data_system/config/events.csv).
- Range (-1,1); positive = BUY.
- DEGENERACY (exact): NO_OVERNIGHT_INVENTORY_SQUEEZE (modules.py:722-733) has the IDENTICAL gate (FRIDAY_CLOSE) and IDENTICAL formula tanh(2*agg_imb) — the two slugs are the same strategy under two names; any evidence for one is evidence for both, and they must never be counted as independent trials. Also degenerate with END_OF_DAY_FORCED_FLATTEN_FLOW (modules.py:687-690) restricted to FRIDAY_CLOSE tapes (its gate is a superset). Flagged in all three specs.

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim: aggressor-flow imbalance inside the FRIDAY_CLOSE window predicts continuation in the de-risking direction over H. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
