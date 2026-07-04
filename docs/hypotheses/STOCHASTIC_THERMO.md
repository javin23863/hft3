# STOCHASTIC_THERMO — hypothesis spec

status: draft-complete
slug: STOCHASTIC_THERMO
kind: pdf_structural | legacy: PDF_MODEL_10
class: `StochasticThermoModel` (packages/features_engine/src/structural_models/model_10_stochastic_thermo.py:53)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Stochastic Thermodynamics / Free Energy

## 1. Market mechanism
Treats competing strategy 'work' as a Gibbs ensemble: the free energy F(beta) is the equilibrium work bound; when observed dissipative work of institutional flow exceeds F plus a threshold, the flow is running beyond its sustainable boundary and mean-reversion is expected (catalog description: "mean-reversion when institutional dissipative work exceeds free-energy boundary"). Diagnostic exhaustion context.

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.
INPUT DEPENDENCY (flagged): with no `observed_dissipative_work` input the model uses a default
work fixture [0, 0.5, 1.0, 1.5] (model_10:62) and mean_reversion_signal is hard False
(:71-73) — no producer of observed dissipative work is wired in the replay pipeline, so the
boolean can never fire there today.

## 2. Signal formula
```
p_i(beta) = exp(-beta*W_i) / Z(beta)                            # model_10:14-25
Z(beta)   = sum exp(-beta*W_i)                                  # :28-35
S = -sum p ln p ; F(beta) = <W> - S/beta                        # :38-50
mean_reversion_signal = observed_dissipative_work >= F + exhaustion_threshold   # :74-76
```
- Payload: StochasticThermoOutput(partition_function, expected_work, entropy, free_energy, mean_reversion_signal).

## 3. Falsifiable prediction
Feature-level (diagnostic) claim: mean_reversion_signal=True predicts reversion of the
preceding institutional-flow direction over H. Untestable in the replay pipeline until an
observed-dissipative-work producer exists (see input dependency above); not in
HORIZON_MAP_PREREGISTERED.json. REFUTED if, with a producer wired, conditional reversion is
indistinguishable from zero on Confirmation years at BH-corrected q=0.10.

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

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: context/diagnostic (catalog role: alpha; blocks_trade: False) — contract routes pdf_structural non-defensive payloads to context_feature
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
