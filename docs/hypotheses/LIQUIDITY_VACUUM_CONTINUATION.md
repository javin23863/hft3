# LIQUIDITY_VACUUM_CONTINUATION — hypothesis spec

status: draft-complete
slug: LIQUIDITY_VACUUM_CONTINUATION
kind: hypothesis | hyp_id: 3 | legacy: HYP_3
class: `LiquidityVacuumContinuation` (packages/features_engine/src/hypotheses/modules.py:229)
execution_role: primary_alpha
display: Liquidity vacuum continuation

## 1. Market mechanism
Market-maker quote machines withdraw depth ahead of/into volatility (their inventory-risk constraint forces them to quote thin), leaving a one-sided vacuum. Aggressors hitting into a vacuum move price disproportionately per contract; the counterparty is the absent liquidity provider who will only re-quote at worse prices. We follow flow into the vacuum and are paid by the mechanical price concession required to refill the book.

## 2. Signal formula
```
signal = tanh(2*liquidity_vacuum_score) * tanh(2*agg_imb)      # modules.py:236-241
```
- Slots: `liquidity_vacuum_score`, `aggressor_volume_imbalance`.
- Range [-1,1]; positive = BUY (flow direction into a thin book). No gating.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 5000 ms, s = 0.12
```

Directional claim: aggressor flow combined with measured book vacuum predicts continuation of mid in the flow direction over 5s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |

Symbols in the declared universe WITHOUT an authoritative spec/fee row (CL, GC, MCL, MGC) are fail-closed: NOT tradable and rejected at intake until instrument_specs.py/fee_model.py gain their rows (no lake data exists for them in this program either).

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'CL', 'MCL', 'GC', 'MGC']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=2468, net=-1609.59, realized=-1308.65, win_rate_filled=0.1621. Old-semantics/v1-expression evidence — NOT model-worth evidence.
