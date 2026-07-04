# MICRO_CONTRACT_RETAIL_LAG — hypothesis spec

status: draft-complete
slug: MICRO_CONTRACT_RETAIL_LAG
kind: hypothesis | hyp_id: 20 | legacy: HYP_20
class: `MicroContractRetailLag` (packages/features_engine/src/hypotheses/modules.py:543)
execution_role: cross_asset_primary_alpha
display: Micro contract retail lag

## 1. Market mechanism
Retail traders in MES react to moves after institutions have already positioned in ES (attention lag, platform notification lag). When ES flow has moved decisively but MES flow has not yet followed, the retail catch-up flow is predictable. We front-run the laggards by following the leader, paid by their late marketable orders.

## 2. Signal formula
```
es_imb = cross_asset['ES'].aggressor_volume_imbalance (0 + absent key -> silent)
div    = micro_leader_divergence(state)        # mes_imb - es_imb
signal = tanh(2*es_imb) * (1 - tanh(|div|))                     # modules.py:567-575
```
- Leader leg: ES `aggressor_volume_imbalance`; helper `micro_leader_divergence` (modules.py:12-34).
- Range (-1,1); positive = BUY MES (follow bullish ES flow while the lag is still open; collapses to 0 once MES has caught up).
- Never ran in Pass A (leader absent).

## 3. Falsifiable prediction
Pre-registered (mechanical, HORIZON_MAP_PREREGISTERED.json):

```
E[ mid(t + H) - mid(t) | signal(t) > s ] > hurdle
H = 15000 ms, s = 0.1
```

Directional claim: decisive ES flow with an unclosed MES lag predicts MES mid catching up toward the ES direction over 15s. REFUTED if the spread-adjusted conditional expectancy E[sign(signal)*(mid(t+H)-mid(t)) - taker spread cost | |signal|>s] fails to exceed the section-4 hurdle on Confirmation years (2021-2022) at BH-corrected q=0.10 over >=40 events (errors two-way clustered by event x calendar month).

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |

Predicted edge at H must exceed the traded symbol's total hurdle or the model is
rejected at intake (template section 4).

## 5. Classification and instrument binding
- Class: offensive (catalog role: None; blocks_trade: False)
- Target universe: ['MES']
- Valid universe: ['ES', 'MES']
- Required leaders: ES | Required sensors: none
- max_round_trips intent: single-shot event trade (v1 evidence: multi-trip machinery mostly idle)

## Evidence ledger
Never ran (lane blocked: leader tapes missing). No economic evidence exists.
