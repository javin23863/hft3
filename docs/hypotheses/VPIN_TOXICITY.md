# VPIN_TOXICITY — hypothesis spec

status: draft-complete
slug: VPIN_TOXICITY
kind: pdf_structural | legacy: PDF_MODEL_3
class: `VPINToxicityModel` (packages/features_engine/src/structural_models/model_03_vpin_toxicity.py:110)
execution_role: defensive_overlay | standalone_hbt_policy: composition_only
display: VPIN Flow Toxicity

## 1. Market mechanism
VPIN (Easley / Lopez de Prado / O'Hara) estimates the probability that current volume is informed (toxic) by measuring buy/sell imbalance in volume-time buckets classified via BVC. When VPIN's percentile is extreme, market makers are being adversely selected and liquidity is about to reprice; the overlay widens spreads and reduces aggression (catalog description) instead of trading.

Why it never trades standalone: kind=pdf_structural with catalog role defensive derives
execution_role=defensive_overlay, standalone_hbt_policy=composition_only
(model_execution_contracts.py:139-157: defensive structural payloads gate execution). The
payload reshapes host quoting/aggression; a standalone run of a risk gate would fabricate
PnL, so the manifest records composition-only receipts (no-cherry-pick v2).
Composition consumers: HYBRID_EXECUTION (VPIN multiplier on the OFI drift term; cancel/passive-to-aggressive flags at VPIN >= 0.5).

## 2. Signal formula
```
BVC buy volume: V_buy = V * StudentT_CDF(dP/sigma, df=5)        # model_03:78-91
VPIN = sum|V_buy - V_sell| / (n * V_bar)  over volume buckets   # :94-107
VPIN_percentile = P(history <= VPIN)  (window 500)              # :171-174
toxic_flow_alert = percentile >= 0.99; regime elevated >= 0.90; vol_warning >= 0.95  # :175-177
```
- Payload: VPINToxicityOutput(VPIN_value, VPIN_percentile, toxicity_regime, toxic_flow_alert, volatility_warning).
- Direction-free by construction: |imbalance| — toxicity says danger, not side.

## 3. Falsifiable prediction
Composed falsifiable claim: high VPIN percentile predicts elevated short-horizon realized
volatility and worse passive-fill markouts (adverse selection). Not in
HORIZON_MAP_PREREGISTERED.json; H must be added mechanically before testing. REFUTED if
realized vol / markout deterioration conditional on percentile >= 0.95 is indistinguishable
from the unconditional baseline on Confirmation years (2021-2022) at BH-corrected q=0.10.

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
