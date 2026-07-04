# QUOTE_PULL_BEFORE_VOLATILITY — hypothesis spec

status: draft-complete
slug: QUOTE_PULL_BEFORE_VOLATILITY
kind: hypothesis | hyp_id: 39 | legacy: HYP_39
class: `QuotePullBeforeVolatility` (packages/features_engine/src/hypotheses/modules.py:862)
execution_role: defensive_overlay | standalone_hbt_policy: composition_only
display: Quote pull before volatility

## 1. Market mechanism
Market makers withdraw quotes seconds before scheduled volatility (macro prints); standing in the book through the pull means being the stale quote snipers pick off. This slug is the catalog's canonical VETO: `blocks_trade: true` — when active it forbids NEW risk rather than proposing any. It is named as the PR-1 defensive veto (`defensive_veto_models:["QUOTE_PULL_BEFORE_VOLATILITY"]`, docs/project/EVENT_ALPHA_REBUILD_PLAN.md:161-163).

Why it never trades standalone: `blocks_trade: true` + `role: defensive` in the catalog (apps/workbench/config/model_catalog.yaml:109-115) derive execution_role=defensive_overlay, standalone_hbt_policy=composition_only (model_execution_contracts.py:141-157). A veto has no standalone PnL by construction — it only removes host orders.

## 2. Signal formula
```
if event_context == 'CPI_TIGHT':
    slope_change = f('book_slope_change')   # computed, then discarded
    return 0.0
return 0.0                                                      # modules.py:869-873
```
- STRUCTURAL NO-OP (flagged): both branches return 0.0; `book_slope_change` is read and discarded, and only the literal 'CPI_TIGHT' context is even inspected. The module math contributes nothing — the operative veto semantics live entirely in the catalog `blocks_trade: true` flag consumed by the composition layer, not in this formula.

## 3. Falsifiable prediction
Composed falsifiable claim (veto value, once a real signal lands): host strategies filtered
by this veto in pre-event windows lose less to adverse selection than unfiltered — measurable
as filled-order markout deltas. As implemented there is NO signal to test (identically zero,
modules.py:869-873); the veto currently fires from the catalog flag/context machinery only.
REFUTATION (for the composed claim): veto-filtered markouts are not better than unfiltered at
BH-corrected q=0.10 on Confirmation years across >=40 events.

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
- Class: defensive veto (catalog role: defensive; blocks_trade: True)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (composition_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
