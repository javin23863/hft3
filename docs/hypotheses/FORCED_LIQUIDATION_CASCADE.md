# FORCED_LIQUIDATION_CASCADE — hypothesis spec

status: draft-complete
slug: FORCED_LIQUIDATION_CASCADE
kind: hypothesis | hyp_id: 7 | legacy: HYP_7
class: `ForcedLiquidationCascade` (packages/features_engine/src/hypotheses/modules.py:181)
execution_role: primary_alpha
display: Forced liquidation cascade

## 1. Market mechanism
Margin-constrained holders (levered retail/prop) are forced to liquidate into stress regardless of price — their risk engine, not information, fires the orders. Signature: one-sided aggressor flow plus blown spread plus cancel panic. The flow is uninformed and temporarily exhausts itself. We fade the cascade direction and are paid by post-liquidation reversion once forced supply clears.

## 2. Signal formula
```
stress = 1 - exp(-max(0, spread_stress - 1.0))
panic  = 1 - exp(-max(0, cancel_to_add_ratio - 1.0))
signal = -tanh(3*agg_imb) * stress * panic                      # modules.py:188-196
```
- Slots: `aggressor_volume_imbalance`, `spread_stress`, `cancel_to_add_ratio`.
- Range (-1,1); positive = BUY (fading a forced sell cascade).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: one-sided flow under simultaneous spread stress and cancel panic predicts reversion against the flow over 15s. Pass A note: the only realized-positive economics of the 27 (+$101 over 210 rows) — thin but directionally consistent. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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

Symbols in the declared universe WITHOUT an authoritative spec/fee row (CL, GC, HG, MCL, MGC, NG, SI) are fail-closed: NOT tradable and rejected at intake until instrument_specs.py/fee_model.py gain their rows (no lake data exists for them in this program either).

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: (none declared — no target constraint; trades any valid-universe symbol)
- Valid universe: ['ES', 'MES', 'NQ', 'MNQ', 'YM', 'MYM', 'RTY', 'M2K', 'CL', 'MCL', 'NG', 'GC', 'MGC', 'SI', 'HG', 'ZN', 'ZB', 'ZF', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Pass A (expression v1, flattened semantics): rows=210, net=79.78, realized=101.11, win_rate_filled=0.1844. Old-semantics/v1-expression evidence — NOT model-worth evidence.
