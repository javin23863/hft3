# REBATE_TRAP_AVOIDANCE — hypothesis spec

status: draft-complete
slug: REBATE_TRAP_AVOIDANCE
kind: hypothesis | hyp_id: 43 | legacy: HYP_43
class: `RebateTrapAvoidance` (packages/features_engine/src/hypotheses/modules.py:451)
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Rebate trap avoidance

## 1. Market mechanism
Intended mechanism (name-level only): avoid resting orders placed to farm maker rebates that become adverse-selection traps. NOTE: CME futures have no maker-rebate economics for this program's account tier — the mechanism is inherited from equities microstructure and has no CME counterpart here; there is no actor who systematically pays us.

## 2. Signal formula
```
return 0.0                                                      # modules.py:458-459
```
- STRUCTURAL NO-OP (flagged): evaluate() returns 0.0 unconditionally; no slots read, no gates. The slug has a name and a registry row but no math.

## 3. Falsifiable prediction
No standalone falsifiable claim — evaluate() is unconditionally zero (modules.py:459)
and the named mechanism has no CME analogue. REFUTATION: not applicable; intake must reject
any activation of this slug until both a mechanism and a formula exist.

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
- Class: no-op implementation (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Not in the PR-0a active set (27 ran + 5 lead-lag). No standalone economics on record for this slug under honest semantics (campaign hbt_stagec3_a326db8f). No model-worth evidence exists.
