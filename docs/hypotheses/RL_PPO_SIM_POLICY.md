# RL_PPO_SIM_POLICY — hypothesis spec

status: draft-complete
slug: RL_PPO_SIM_POLICY
kind: reinforcement_learning | legacy: RL_PPO_SIM
implementation: NOT FOUND — spec is contract-only (registry class `PPOSimPolicyArtifact` has no trainer; rl_status: blocked_sim_env_required)
execution_role: rl_research_blocked | standalone_hbt_policy: blocked_not_order_strategy
display: PPO Simulation Policy

**implementation: NOT FOUND — spec is contract-only.** No PPO trainer exists in the repo (searched packages/research_pipeline and scripts; only deep-Q proxies exist). The registry entry declares `rl_status: blocked_sim_env_required` — PPO is an online policy-gradient algorithm requiring an interactive replay simulation environment that has not been built.

## 1. Market mechanism
Placeholder for a future online PPO policy trained in an interactive replay simulation (source_family interactive_replay_simulation; action space hold/enter_long/enter_short). No market-mechanism claim, no trainer, no artifact — the registry row exists so the catalog is exhaustive and the blocker is explicit rather than silent.

Why it never trades standalone: kind=reinforcement_learning is hard-mapped to
execution_role=rl_research_blocked, standalone_hbt_policy=blocked_not_order_strategy
(model_execution_contracts.py:146-147, 94). RL artifacts are research outputs with
promotion_status=blocked_downstream_validation_required in the registry; the runner emits an
explicit semantic blocker for them — they are not order strategies and produce no standalone
PnL rows (no-cherry-pick v2).

## 2. Signal formula
No signal formula and no implementation (see banner). Authority refs (registry entry):
docs/project/REINFORCEMENT_LEARNING_IMPLEMENTATION_PLAN.md, vault RL source map + budget rule
2026-06-25.

## 3. Falsifiable prediction
No standalone falsifiable claim — contract-only receipt. Nothing exists to test; the slug is
doubly blocked (rl_research_blocked role AND blocked_sim_env_required status).

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
- Class: RL research placeholder (rl_status: blocked_sim_env_required; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['ES', 'M2K', 'MES', 'MNQ', 'MYM', 'NQ', 'RTY', 'YM', 'ZB', 'ZF', 'ZN', 'ZT']
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (blocked_not_order_strategy)

## Evidence ledger
No artifact has ever been trained (no trainer exists). Research artifacts only; promotion_status blocked_downstream_validation_required. No trading evidence exists and none may be claimed while the slug is rl_research_blocked.
