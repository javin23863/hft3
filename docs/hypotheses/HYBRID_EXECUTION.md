# HYBRID_EXECUTION — hypothesis spec

status: draft-complete
slug: HYBRID_EXECUTION
kind: pdf_structural | legacy: PDF_MODEL_4
class: `HybridExecutionModel` (packages/features_engine/src/structural_models/model_04_hybrid_execution.py:46)
execution_role: execution_engine | standalone_hbt_policy: composition_only
display: Hybrid Avellaneda-Stoikov Execution

## 1. Market mechanism
Avellaneda-Stoikov market-making machinery extended with an OFI drift term and a VPIN toxicity multiplier: the reservation price leans with measured book pressure, and lean plus quote-pull aggressiveness scale up as flow turns toxic. It is a QUOTE ENGINE — it decides where/how a host strategy quotes; it holds no view of its own.

Why it never trades standalone: slug is in PDF_HYBRID_REPLAY, hard-mapped to
execution_role=execution_engine, standalone_hbt_policy=composition_only
(model_execution_contracts.py:49,147-148,86-95). An execution engine without a host signal
has no trade to place; the manifest records composition-only receipts. Catalog dependencies:
requires BOOK_PRESSURE and VPIN_TOXICITY (apps/workbench/config/model_catalog.yaml:32-41).

## 2. Signal formula
```
r(t) = S_t - q_t*gamma*sigma^2*(T-t)                            # model_04:12-21
spread = gamma*sigma^2*(T-t) + (2/gamma)*ln(1 + gamma/kappa)    # :24-30
lambda_t = lambda_scale * (1 + VPIN); r*(t) = r(t) + lambda_t*OFI_smooth   # :33-43
optimal_bid/ask = r* -/+ spread/2                               # :74,82-83
cancel_quote = VPIN >= 0.5 and |inventory| > 1; passive_to_aggressive = VPIN >= 0.5   # :76-77
```
- Payload: HybridExecutionOutput(reservation_price, hybrid_reservation_price, optimal_bid/ask, spread_width, inventory_penalty, OFI_drift_component, VPIN_multiplier, cancel_quote_flag, passive_to_aggressive_flag).
- Defaults: gamma 0.1, kappa 1.5, sigma 0.02, T 3600s, lambda_scale 0.001 (:49-56).

## 3. Falsifiable prediction
No standalone falsifiable claim — composition receipt only. This is pure execution
infrastructure: it emits quote placements, not directional views. The testable composed claim
(host strategies quoted through this engine achieve better net-of-cost fills than naive
touch-quoting) belongs to the composition harness, not to this slug's intake.

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
- Class: execution infrastructure (catalog role: defensive; blocks_trade: False; requires: BOOK_PRESSURE, VPIN_TOXICITY)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (composition_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
