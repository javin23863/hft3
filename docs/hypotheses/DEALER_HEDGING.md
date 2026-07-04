# DEALER_HEDGING — hypothesis spec

status: draft-complete
slug: DEALER_HEDGING
kind: pdf_structural | legacy: PDF_MODEL_5
class: `DealerHedgingModel` (packages/features_engine/src/structural_models/model_05_dealer_hedging.py:68)
execution_role: options_fixture | standalone_hbt_policy: diagnostic_only
display: Dealer GEX / Vanna / Charm

## 1. Market mechanism
Options dealers who are net short gamma must hedge in the direction of the move (destabilizing) and vice versa; aggregate dealer gamma/vanna/charm exposures computed from an options chain locate the zero-gamma level and the sign of mechanical hedging pressure on the underlying. Vol-regime context for equity-index futures.

Why it never trades standalone: slug is in PDF_OPTIONS_FIXTURE, hard-mapped to
execution_role=options_fixture, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:50,149-150,86-95). Inputs are CHAIN FIXTURES (catalog:
"from chain fixtures", apps/workbench/config/model_catalog.yaml:42-48) — this program has no
live options-chain feed, so payloads are fixture-driven diagnostics by construction.

## 2. Signal formula
```
d1,d2 = BS terms; gamma = phi(d1)/(S*sigma*sqrt(t))             # model_05:20-33
vanna = -phi(d1)*d2/sigma ; charm = -phi(d1)*(2rt - d2*sigma*sqrt(t))/(2t*sigma*sqrt(t))   # :36-48
GEX = -gamma*OI*S^2*mult*0.01  (dealer-short sign convention)   # :51-53, 93-94
zero_gamma_level = strike where cumulative GEX flips sign       # :56-65
dealer_hedging_pressure = -total_GEX/|total_GEX|                # :101
```
- Payload: DealerHedgingOutput(total_gex, gex_by_strike, vanna_exposure, charm_exposure, zero_gamma_level, dealer_hedging_pressure).

## 3. Falsifiable prediction
No standalone falsifiable claim — fixture-driven diagnostic receipt only. The would-be claim
(negative total GEX predicts higher realized vol and momentum-amplifying hedge flow) requires
a real options-chain feed this program does not ingest; until one exists any test would be
fixture-on-fixture. Intake records this spec as context documentation.

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

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: options fixture / diagnostic (catalog role: hybrid; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
