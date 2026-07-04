# RL_EXECUTION_POLICY — hypothesis spec

status: draft-complete
slug: RL_EXECUTION_POLICY
kind: reinforcement_learning | legacy: RL_EXECUTION
class: `RLExecutionPolicyArtifact` (registry artifact class; trainer: packages/research_pipeline/rl_agents.py:154 `train_rl_policy_artifact`)
execution_role: rl_research_blocked | standalone_hbt_policy: blocked_not_order_strategy
display: Reinforcement Learning Execution Policy

## 1. Market mechanism
Research artifact: tabular Q-learning over microstructure policy rows (algorithm `tabular_q_learning_research_cpu_smoke`, rl_agents.py:27; action space hold/enter_long/enter_short). There is no market mechanism claim and no counterparty who pays us — the artifact exists to exercise the offline-RL plumbing (artifact schema hft3_rl_policy_artifact_v1).

Why it never trades standalone: kind=reinforcement_learning is hard-mapped to
execution_role=rl_research_blocked, standalone_hbt_policy=blocked_not_order_strategy
(model_execution_contracts.py:146-147, 94). RL artifacts are research outputs with
promotion_status=blocked_downstream_validation_required in the registry; the runner emits an
explicit semantic blocker for them — they are not order strategies and produce no standalone
PnL rows (no-cherry-pick v2).

## 2. Signal formula
No signal formula — the slug is a policy ARTIFACT, not a feature transform. Authority refs
(registry entry): docs/project/REINFORCEMENT_LEARNING_IMPLEMENTATION_PLAN.md,
packages/research_pipeline/rl_agents.py, vault RL decisions 2026-06-25. Training entry point:
`train_rl_policy_artifact` / `train_or_load_rl_policy_artifact` (rl_agents.py:154, 240).

## 3. Falsifiable prediction
No standalone falsifiable claim — rl_research_blocked receipt only. Any future promotion
must first pass downstream validation (promotion_status gate) and then register a horizon and
threshold mechanically; until then no market prediction exists to refute.

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
- Class: RL research artifact (rl_status: offline_rl_research_only; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (blocked_not_order_strategy)

## Evidence ledger
Research artifacts only; promotion_status blocked_downstream_validation_required. No trading evidence exists and none may be claimed while the slug is rl_research_blocked.
