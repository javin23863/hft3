# RL_DEEP_Q_EXECUTION_PROXY — hypothesis spec

status: draft-complete
slug: RL_DEEP_Q_EXECUTION_PROXY
kind: reinforcement_learning | legacy: RL_DEEP_Q_PROXY
class: `DeepQExecutionProxyArtifact` (registry artifact class; trainer: packages/research_pipeline/rl_agents.py:406 `train_deep_rl_policy_artifact`; campaign runner: scripts/run_rl_gpu_campaign_npz_fast.py)
execution_role: rl_research_blocked | standalone_hbt_policy: blocked_not_order_strategy
display: Deep-Q Execution Proxy

## 1. Market mechanism
Research artifact: offline deep-Q proxy trained with vectorized MSE on fs_v1_target feature rows (algorithm `offline_deep_q_proxy_mse_vectorized`; supported features: order_book_imbalance, queue_imbalance, order_flow_imbalance, micro_price, spread; action space hold/enter_long/enter_short). Registry flags it honestly as `algorithm_status: pre_ppo_deep_q_proxy_not_rl5_completion` — a proxy, not a completed RL5 policy. No market-mechanism claim.

Why it never trades standalone: kind=reinforcement_learning is hard-mapped to
execution_role=rl_research_blocked, standalone_hbt_policy=blocked_not_order_strategy
(model_execution_contracts.py:146-147, 94). RL artifacts are research outputs with
promotion_status=blocked_downstream_validation_required in the registry; the runner emits an
explicit semantic blocker for them — they are not order strategies and produce no standalone
PnL rows (no-cherry-pick v2).

## 2. Signal formula
No signal formula — policy artifact (schema hft3_rl_gpu_vectorized_campaign_v1). Authority
refs (registry entry): docs/project/REINFORCEMENT_LEARNING_IMPLEMENTATION_PLAN.md,
scripts/run_rl_gpu_campaign_npz_fast.py, vault RL source map + H200 campaign receipt
2026-06-26. Universe uses Databento research symbols (ES.v.0 etc.), normalized by
instrument_specs.normalize_product (instrument_specs.py:65-74).

## 3. Falsifiable prediction
No standalone falsifiable claim — rl_research_blocked receipt only; additionally the
registry marks the algorithm a pre-PPO proxy. Promotion requires downstream validation plus a
mechanically registered horizon; no market prediction exists to refute today.

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

| symbol | fee/side $ | multiplier | fee hurdle (pts) | fee hurdle (ticks) | + 1 tick taker slippage (ticks RT) |
|---|---|---|---|---|---|
| ES | 1.52 | 50 | 0.0608 | 0.243 | 1.243 |
| MES | 0.52 | 5 | 0.2080 | 0.832 | 1.832 |
| MNQ | 0.52 | 2 | 0.5200 | 2.080 | 3.080 |
| NQ | 1.52 | 20 | 0.1520 | 0.608 | 1.608 |
| RTY | 1.52 | 50 | 0.0608 | 0.608 | 1.608 |
| ZB | 1.07 | 1000 | 0.0021 | 0.068 | 1.068 |
| ZN | 1.07 | 1000 | 0.0021 | 0.137 | 1.137 |

This slug is diagnostic-only and places no orders, so no order ever pays this hurdle
directly. The table is the intake authority for any FUTURE composition that consumes this
payload: a composed strategy must clear the traded symbol's total hurdle (template section 4).

## 5. Classification and instrument binding
- Class: RL research artifact (rl_status: offline_rl_research_only; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES.v.0', 'MES.v.0', 'MNQ.v.0', 'NQ.v.0', 'RTY.v.0', 'ZB.v.0', 'ZN.v.0'] (research symbols; products via normalize_product)
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (blocked_not_order_strategy)

## Evidence ledger
Research artifacts only; promotion_status blocked_downstream_validation_required. No trading evidence exists and none may be claimed while the slug is rl_research_blocked.
