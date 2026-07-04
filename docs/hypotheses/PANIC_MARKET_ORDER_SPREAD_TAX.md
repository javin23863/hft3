# PANIC_MARKET_ORDER_SPREAD_TAX — hypothesis spec

status: draft-complete
slug: PANIC_MARKET_ORDER_SPREAD_TAX
kind: hypothesis | hyp_id: 28 | legacy: HYP_28
class: `PanicMarketOrderSpreadTax` (packages/features_engine/src/hypotheses/modules.py:214)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Panic market-order spread tax

## 1. Market mechanism
Intended mechanism (docstring intent): in high-volatility spread stress, panicked market orders pay an outsized spread tax; a passive join on the receiving side earns that tax. The payer is the panicking taker.

## 2. Signal formula
```
vol_multiplier = 1.0 if volatility_state == 'HIGH' else 0.0
activation = 1 - exp(-max(0, spread_stress - 1.5))
return vol_multiplier * activation * 0.0                        # modules.py:224-227
```
- STRUCTURAL NO-OP (flagged): the return is multiplied by 0.0 — the signal is identically zero on every path. The activation math is computed and discarded. The intended passive-join edge is never expressed; this slug currently cannot fire.

## 3. Falsifiable prediction
No standalone falsifiable claim as implemented — evaluate() is identically zero
(modules.py:227, `* 0.0`). The section-1 mechanism is untestable through this slug until a
real signal formula lands. REFUTATION: not applicable; intake must reject any activation of
this slug while the formula is a no-op.

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
- Class: offensive-intent, no-op implementation (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Not in the PR-0a active set (27 ran + 5 lead-lag). No standalone economics on record for this slug under honest semantics (campaign hbt_stagec3_a326db8f). No model-worth evidence exists.
