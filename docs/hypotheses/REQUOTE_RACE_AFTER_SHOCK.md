# REQUOTE_RACE_AFTER_SHOCK — hypothesis spec

status: draft-complete
slug: REQUOTE_RACE_AFTER_SHOCK
kind: hypothesis | hyp_id: 40 | legacy: HYP_40
class: `RequoteRaceAfterShock` (packages/features_engine/src/hypotheses/modules.py:875)
execution_role: defensive_overlay | standalone_hbt_policy: composition_only
display: Re-quote race after shock

## 1. Market mechanism
Milliseconds-to-seconds after a volatility shock, market makers race to re-quote; the side of the book that rebuilds first (book-slope change direction) reveals where liquidity providers are confident. The overlay reads the rebuild direction so hosts can skew reservation prices while liquidity reprices (catalog description: "adjusts reservation skew while liquidity reprices post-vol spike").

Why it never trades standalone: the workbench catalog marks this slug `role: defensive`
(apps/workbench/config/model_catalog.yaml), so the semantic contract derives
execution_role=defensive_overlay and standalone_hbt_policy=composition_only
(model_execution_contracts.py:141-157, 86-95). Defensive overlays gate or reshape a host
strategy's orders; a standalone run would fabricate PnL for a slug whose job is vetoing and
skewing, so the runner emits a composition-only receipt instead (no-cherry-pick v2).

## 2. Signal formula
```
if regime_state == 'event_shock':
    signal = tanh(3*book_slope_change)                          # modules.py:882-886
else: signal = 0
```
- Slot: `book_slope_change`; regime gate `event_shock` (regime_filter.py:75).
- Range (-1,1); positive = BUY-side rebuild.

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Composed falsifiable claim: post-shock book-slope rebuild direction predicts mid drift toward the rebuilding side over H (testable as feature IC). REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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

This slug never places standalone orders; the fee/slippage hurdle above is charged to the
HOST strategy's orders whenever this overlay gates, skews, or re-quotes them. Any composed
claim must clear the host symbol's total hurdle net of the overlay's effect (template section 4).

## 5. Classification and instrument binding
- Class: defensive (catalog role: defensive; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (composition_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
