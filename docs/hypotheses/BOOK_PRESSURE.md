# BOOK_PRESSURE — hypothesis spec

status: draft-complete
slug: BOOK_PRESSURE
kind: pdf_structural | legacy: PDF_MODEL_1
class: `BookPressureModel` (packages/features_engine/src/structural_models/model_01_book_pressure.py:102)
execution_role: context_feature | standalone_hbt_policy: diagnostic_only
display: Limit Order Book Pressure (OFI / MLOFI / PCA)

## 1. Market mechanism
Order-flow imbalance at and near the touch (Cont-Kukanov-Stoikov class) measures net quoted-liquidity pressure; persistent positive OFI precedes bid-side continuation because the book must reprice to clear the imbalance. As a context feature it tells the stack which way the book is leaning and whether the lean is spoofed (L1 vs deep-book PCA disagreement).

Why it never trades standalone: kind=pdf_structural and the slug is not defensive, so the
semantic contract assigns execution_role=context_feature, standalone_hbt_policy=diagnostic_only
(model_execution_contracts.py:151-154: "Structural payloads never become standalone order
signals"). It emits a typed payload consumed as environment state; the manifest records a
diagnostic receipt, never standalone PnL.
Composition consumers: HYBRID_EXECUTION (OFI_smooth drift term), CROSS_ASSET_LEAD_LAG and DOW_YM_INDEX (leader/constituent OFI inputs).

## 2. Signal formula
```
level OFI (bid): +q_curr if price up; -q_prev if price down; dq if unchanged   # model_01:16-37
ask side enters with opposite sign; L1 event e = bid_contrib + ask_contrib     # :40-52
OFI_cum += e; OFI_zscore = (e - mean)/std over 50-event window                 # :140,156-161
MLOFI^m = OF_m_bid - OF_m_ask per level (m<=5)                                 # :63-80
MLOFI_PC1 = PC1 score of MLOFI history via SVD                                 # :83-99
spoofing_risk_flag = sign(L1) != sign(PC1) and |z| >= 0.5                      # :167-172
```
- Payload: BookPressureOutput(OFI_value, OFI_zscore, OFI_smooth, MLOFI_vector, MLOFI_PC1, book_pressure_direction, spoofing_risk_flag).
- Positive OFI = bid-side pressure (BUY lean).

## 3. Falsifiable prediction
Feature-level (diagnostic) claim — this slug never places orders, so the test is IC, not PnL:

```
E[ mid(t + H) - mid(t) | OFI_smooth(t) > s ] > 0     (H, s: to be pre-registered mechanically)
```

Not in HORIZON_MAP_PREREGISTERED.json (32 PR-0a active models only); H and s must be added by
the same mechanical rule before testing. REFUTED if the conditional expectancy is
indistinguishable from zero on Confirmation years (2021-2022) at BH-corrected q=0.10 over
>=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: context/diagnostic (catalog role: alpha; blocks_trade: False) — catalog says alpha, contract routes pdf_structural non-defensive payloads to context_feature
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'MES', 'MNQ', 'NQ', 'ZB', 'ZN']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (diagnostic_only)

## Evidence ledger
No standalone evidence by contract: manifest/evidence-ledger rows for this slug are composition/diagnostic receipts or semantic blockers, never standalone PnL (no-cherry-pick v2, model_execution_contracts.py:1-24).
