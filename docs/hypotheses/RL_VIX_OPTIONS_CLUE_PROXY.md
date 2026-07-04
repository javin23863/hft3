# RL_VIX_OPTIONS_CLUE_PROXY — hypothesis spec

status: draft-complete
slug: RL_VIX_OPTIONS_CLUE_PROXY
kind: reinforcement_learning | legacy: RL_VIX_OPTIONS_CLUE
class: `VixOptionsClueRLProxyArtifact` (registry artifact class; manifest builder: scripts/build_vix_options_rl_manifest.py; campaign runner: scripts/run_rl_gpu_campaign_npz_fast.py)
execution_role: rl_research_blocked | standalone_hbt_policy: blocked_not_order_strategy
display: VIX Options Clue RL Proxy

## 1. Market mechanism
Research artifact: offline deep-Q proxy over VIX options clue rows (source_family vix_options_clue; action space hold/clue_up/clue_down — it emits CLUES about VIX direction, not orders, by construction). No market-mechanism claim; the artifact exercises the VIX-options clue schema path.

Why it never trades standalone: kind=reinforcement_learning is hard-mapped to
execution_role=rl_research_blocked, standalone_hbt_policy=blocked_not_order_strategy
(model_execution_contracts.py:146-147, 94). RL artifacts are research outputs with
promotion_status=blocked_downstream_validation_required in the registry; the runner emits an
explicit semantic blocker for them — they are not order strategies and produce no standalone
PnL rows (no-cherry-pick v2).

## 2. Signal formula
No signal formula — policy artifact (schema hft3_rl_gpu_vectorized_campaign_v1, algorithm
offline_deep_q_proxy_mse_vectorized, pre-PPO proxy status). Authority refs (registry entry):
docs/project/REINFORCEMENT_LEARNING_IMPLEMENTATION_PLAN.md, scripts/build_vix_options_rl_manifest.py,
scripts/run_rl_gpu_campaign_npz_fast.py, vault VIX-options RL decisions 2026-06-26.

## 3. Falsifiable prediction
No standalone falsifiable claim — rl_research_blocked receipt only, and the action space is
clue-emission, not order placement. No market prediction exists to refute.

## 4. Cost hurdle (authoritative: instrument_specs.py + fee_model.py, non-member tier)

Universe is `VIX.OPT` only — NOT in instrument_specs.py `_SPEC_ROWS` (instrument_specs.py:31-52)
and NOT in FeeModel.FEES (fee_model.py:32-52): fee/tick resolution FAILS CLOSED
(`instrument_spec_missing:VIX.OPT` / `fee_model_unknown_product`). No hurdle table can be
computed, which is itself the receipt that this slug can never be priced as an order strategy
in this program.

## 5. Classification and instrument binding
- Class: RL research artifact (rl_status: offline_rl_research_only; blocks_trade: False)
- Target universe: (none declared — no target constraint)
- Valid universe: ['VIX.OPT'] (not resolvable by instrument_specs — fail-closed)
- Required leaders: none | Required sensors: none
- max_round_trips intent: not applicable — this slug never enters the standalone order queue (blocked_not_order_strategy)

## Evidence ledger
Research artifacts only; promotion_status blocked_downstream_validation_required. No trading evidence exists and none may be claimed while the slug is rl_research_blocked.
