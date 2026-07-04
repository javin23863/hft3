# GHOST_ROUTE — hypothesis spec

status: draft-complete
slug: GHOST_ROUTE
kind: hypothesis | hyp_id: 45 | legacy: HYP_45
class: `GhostRoute` (packages/features_engine/src/hypotheses/modules.py:888); full event-time implementation: models/ghost_route/ghost_route_model.py
execution_role: primary_alpha | standalone_hbt_policy: standalone_executable
display: Ghost Route MBO queue-decay

## 1. Market mechanism
Level-3 MBO queue decay on a macro contract (ES/NQ/YM) — near-touch cancels and downward modifies without trades — signals that the queue is being abandoned ahead of a move. Micro-contract (MES/MNQ/MYM) quotes lag and remain stale after realistic feed+order latency; a FAK limit order picks off the stale micro quote before it reprices. The payer is the slower micro-side quoter; the equalizer is queue-decay math, not raw speed.

## 2. Signal formula
Registry-path evaluate (modules.py:899-906) — STRUCTURAL NO-OP on the 64-slot replay:
```
if expected_edge_ticks <= 0: return 0
signal = clip(tanh(macro_shadow_decay) * tanh(micro_stale_quote_zscore) * sign(macro_nofi), -1, 1)
```
- The four slots (`macro_shadow_decay`, `micro_stale_quote_zscore`, `macro_nofi`, `expected_edge_ticks`) are NOT in FEATURE_NAME_TO_INDEX (packages/features_engine/src/features/feature_index.py) — `state.f` returns the 0.0 default, the `edge <= 0` gate always trips, and the registry-path signal is identically zero. The slot-based adapter is an honest routing stub, not the model.

Real math (models/ghost_route/ghost_route_model.py):
```
shadow = (cancel_vol + modify_down_vol - add_vol) - trade_vol            # :349-350
nsd    = shadow / max(Q0, eps)                                           # :352
decay_event = nsd >= tau_decay_norm and trade_vol <= eps_trade
              and remaining <= tau_remaining and cancel/trade >= tau_ctr # :355-360
direction: bid decay_event and nOFI <= -tau  -> SELL micro; ask/+tau -> BUY  # :555-561
edge  = max(|spread_zscore| - tau_z, 0)
        - (crossing + fees + slippage + adverse_selection + miss)        # :428-444
order only if stale_quote and edge >= min_expected_edge_ticks (0.25)     # :62,570-571
```

## 3. Falsifiable prediction
NOT pre-registered: this slug is absent from docs/hypotheses/HORIZON_MAP_PREREGISTERED.json
(the committed authority covers only the 32 PR-0a active models). Before any IC test, H and s
MUST be added there by the same mechanical rule (modal holding_period_bars x 1000ms interval
from the envelope; zero researcher choice). Until then the claim below is a form, not a
registered test.

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle      (H, s: to be pre-registered mechanically)
```

Directional claim: macro shadow-decay events with confirming normalized OFI predict that the paired micro quote is stale and executable at positive expected edge after modeled latency and costs. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

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
Note: the ghost-route harness already nets its OWN cost stack inside expected_edge_ticks
(crossing + fees + slippage + adverse selection + miss penalty, ghost_route_model.py:436-443);
the table above is the registry-side authority and must not be double-charged on top of it.

## 5. Classification and instrument binding
- Class: offensive (catalog role: alpha; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Research-only harness (models/ghost_route, own backtest + event-log schema). The registry-path adapter has never produced a standalone signal (structural no-op above); no economic evidence exists on the unified pipeline.
