# ECONOMIC_EVENT_RESTRICTION_FLATTENING — hypothesis spec

status: draft-complete
slug: ECONOMIC_EVENT_RESTRICTION_FLATTENING
kind: hypothesis | hyp_id: 38 | legacy: HYP_38
class: `EconomicEventRestrictionFlattening` (packages/features_engine/src/hypotheses/modules.py:822)
execution_role: primary_alpha
display: Economic-event restriction flattening

## 1. Market mechanism
Prop firms ban trading in a window around scheduled macro releases; traders still positioned at the ban must flatten immediately regardless of price — a rule-driven, information-free forced unwind. The news_restriction_flatten_score measures that one-sided forced flow (near-touch cancels x one-sided flow). We ride the forced-exit direction, paid by the mechanical pressure of rule-bound flatteners.

## 2. Signal formula
```
if not event_context.endswith('_TIGHT'): return 0
signal = tanh(2*news_restriction_flatten_score)                 # modules.py:854-860
```
- Slot: `news_restriction_flatten_score` (slot 31); gated to `_TIGHT` event windows ([-60s,+10s] around releases).
- Range (-1,1); positive = BUY (forced buying pressure).
- NOTE: every campaign tape IS a _TIGHT window, so the gate always passes in our replay universe — the gate has no discriminating power on this data; the slot value carries the entire signal.

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: measured forced-flatten flow inside the news-ban window predicts continuation in the flatten direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Pass A (expression v1, flattened semantics): rows=6800, net=-11470.02, realized=-7773.44, win_rate_filled=0.2701. Old-semantics/v1-expression evidence — NOT model-worth evidence.
