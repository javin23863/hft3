---
date: 2026-07-03
status: draft
area: hftbacktest, model-catalog, semantic-routing
repo_worktree: C:\Users\MSI\repos\hft3-fix-wt
branch_observed: fix/leader-pool-replay-filter
owner_request: "Fix the whole catalog: all models, not only Hawkes / second-wave examples."
graph_gate: waived-by-owner-2026-06-16
---

# HBT Catalog Semantic Routing Fix Plan

## 0. Grounding receipt

This plan is grounded in these artifacts read during the planning pass:

- Repo gate: `AGENTS.md`.
- Fable: `docs/vault/FABLE_MINDSET.md`, `.cursor/rules/00-fable-mindset.mdc`.
- Ponytail: `.cursor/rules/01-ponytail-mindset.mdc`, `docs/ai/PONYTAIL.md`.
- Vault: `wiki/hot.md`, `wiki/money-path.md`, `Home.md`, `Memory Stack.md`.
- Ontology: `library/Ontology.md`, `library/System Implications.md`.
- Decision authority: `decisions/2026-06-29 HBT-only all-model uniform-flow rule.md`.
- Repo project plans: `docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md`, `docs/project/HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md`, `docs/project/VBT_MODEL_ONTOLOGY.md`, `docs/project/HYPOTHESIS_SPEC_TEMPLATE.md`.
- Catalog/config authority: `packages/features_engine/config/model_registry.yaml`, `apps/workbench/config/models.yaml`, `apps/workbench/config/model_catalog.yaml`, `apps/workbench/config/model_event_binding.yaml`.
- Code surfaces inspected: `packages/backtest_pipeline/src/hftbacktest_only_campaign_manifest.py`, `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py`, `packages/backtest_pipeline/src/pipeline_model_router.py`, `packages/replay/cross_asset_assembly.py`, `packages/features_engine/src/hypotheses/modules.py`, `packages/features_engine/src/structural_models/model_11_hawkes_toxic.py`, `packages/features_engine/src/pipeline/structural_integration.py`.
- Tests inspected: `tests/backtest_pipeline/test_hftbacktest_only_campaign_manifest.py`, `tests/backtest_pipeline/test_hftbacktest_only_pipeline.py`, `tests/backtest_pipeline/test_pipeline_model_router.py`, `tests/test_cross_asset_assembly.py`, `tests/test_research_pipeline.py`, `tests/test_workbench/test_model_catalog.py`, `tests/test_workbench/test_registry.py`.

Working tree was dirty before this plan was written. Observed dirty paths included prior HBT/graph/runtime work. Do not reset/stash/clean without owner instruction.

**Code-verification pass (2026-07-03, post-draft):** the load-bearing claims in section 1 were re-checked against source and confirmed at exact lines — `_structural_payload_signal` tanh -> BUY/SELL at `hftbacktest_only_pipeline.py:1803-1814` with side pick at `:1038`; runner flatten at `run_hftbacktest_only_campaign.py:33,163,194-195`; no semantic/execution-role columns in the manifest (`REQUIRED_ROW_FIELDS:61-89`, `_build_row:1371-1402`); `_infer_adapter_statuses` is adapter-availability introspection, not a role graph (`:1710-1717`); inventory 65 = 50 hypothesis / 11 pdf_structural / 4 reinforcement_learning. The same pass found infrastructure the draft understated; sections 4, 6.1 (Phase 1), and 6.4 (Phase 4) were corrected accordingly.

## 1. Problem statement

The current HBT-only campaign correctly rejected the old VectorBT/Stage-A selector, but it overcorrected. The 2026-06-29 no-cherry-pick decision conflated two different requirements:

1. **Inventory coverage:** every canonical registry slug must appear in campaign/evidence ledgers.
2. **Standalone executability:** every slug can be converted into a `hypothesis_limit_order` BUY/SELL strategy.

Requirement 1 is still correct. Requirement 2 is false.

The active runner/manifest path currently lets adapter availability stand in for semantic executability. In particular:

- `hftbacktest_only_campaign_manifest._infer_adapter_statuses` checks uniform adapter status, not a model-role graph.
- The runner sets `strategy_id = hypothesis_limit_order` and injects `strategy_params["model_id"] = canonical_model_id`.
- `_structural_payload_signal()` can convert structural payload fields into signs and thresholds, e.g. `reservation_price_skew -> tanh(value) -> BUY/SELL`.
- `valid_instrument_universe` / `target_instrument_universe` are not enforced in the HBT manifest/runner path.
- Missing leader/sensor context can become `signal_below_threshold` or no-order evidence rather than an honest un-runnable semantic/data blocker.

Result: defensive, diagnostic, execution-engine, cross-asset clue, sensor-context, RL proxy, and primary alpha models were flattened into one PnL-ranked bucket.

## 2. Corrected principle

### No-cherry-pick v2

```text
Every canonical slug appears in the manifest/evidence ledger.
Only semantically executable standalone strategies enter the standalone HBT order queue.
Non-standalone slugs emit explicit semantic/data/authority blocker or composition-only receipts.
No omitted rows. No fake model rejection. No fake standalone PnL evidence.
```

This is a refinement/supersession of the 2026-06-29 phrase that registry `kind` / model role must not route active HBT units. The replacement rule is:

- model role **may not omit** a slug from the catalog ledger;
- model role **must** decide the execution surface: standalone alpha run, composition-only overlay, diagnostic evaluation, sensor/leader-blocked row, options fixture, RL research blocker, or execution-engine path.

Clock / metric / authority:

- **Clock:** event-time HBT replay; distinguish pre-HBT semantic admissibility from post-HBT economic evaluation.
- **Metric:** semantic coverage count, blocker-code correctness, runnable-queue correctness, then orders/fills/PnL only for true standalone-executable units.
- **Authority:** HBT-only identity fields, model registry/catalog, HYP spec template section 5, workbench defensive composition, PDF structural docs, `System Implications` enforcement map.

## 3. Full catalog inventory observed

Inventory command over `model_registry.yaml` returned:

```text
TOTAL 65
BY_KIND {'hypothesis': 50, 'pdf_structural': 11, 'reinforcement_learning': 4}
```

All 65 slugs must be covered by tests and manifest contracts.

## 4. Proposed execution-role taxonomy

Add one central semantic contract layer, preferably extending existing registry/catalog data rather than creating a parallel taxonomy.

**Reconcile with the existing router categories first.** `packages/backtest_pipeline/src/pipeline_model_router.py:12-17` already defines role frozensets — `PDF_STRUCTURAL_EVAL`, `PDF_DIAGNOSTICS`, `PDF_HYBRID_REPLAY`, `PDF_OPTIONS_FIXTURE`, `SMOKE_HYP_SAMPLE`. They are currently metadata-only and gate nothing in the HBT runner. The contract layer must absorb these sets as its source of role truth (or delete them in the same change and re-home their membership on the contract). Do not stand up a second taxonomy beside them — that is the parallel-taxonomy failure this section is meant to avoid.

Suggested fields / dataclass shape:

```python
@dataclass(frozen=True)
class ModelExecutionContract:
    canonical_model_id: str
    kind: str
    execution_role: Literal[
        "primary_alpha",
        "cross_asset_primary_alpha",
        "sensor_conditioned_primary_alpha",
        "defensive_overlay",
        "execution_engine",
        "context_feature",
        "options_fixture",
        "rl_research_blocked",
    ]
    standalone_hbt_policy: Literal[
        "standalone_executable",
        "requires_leader_tape",
        "requires_sensor_tape",
        "composition_only",
        "diagnostic_only",
        "blocked_missing_adapter",
        "blocked_not_order_strategy",
    ]
    valid_instrument_universe: tuple[str, ...]
    target_instrument_universe: tuple[str, ...]
    required_leaders: tuple[str, ...]
    required_sensors: tuple[str, ...]
    requires_models: tuple[str, ...]
    blocks_trade: bool
    authority_refs: tuple[str, ...]
```

Then the manifest emits both inventory rows and semantic admissibility:

```text
semantic_execution_role
standalone_hbt_policy
semantic_admissibility_status
semantic_blocker_code
semantic_blocker_detail
required_leaders
required_sensors
target_instrument_universe
valid_instrument_universe
composition_requirements
```

## 5. Initial whole-catalog handling matrix

This is the initial routing target. It is intentionally conservative: if a slug is not clearly a standalone directional order strategy, it is ledgered but not traded standalone until a spec says how it produces BUY/SELL, on what target, with what horizon and cost hurdle.

| # | Model | Kind | Initial HBT handling | Required semantic checks |
|---:|---|---|---|---|
| 1 | `SECOND_WAVE_CONTINUATION` | hypothesis | `primary_alpha` | enforce valid universe; product-specific thresholds/horizons |
| 2 | `STOP_RUN_EXHAUSTION_FADE` | hypothesis | `primary_alpha` | enforce valid universe; fade sign/horizon spec |
| 3 | `LIQUIDITY_VACUUM_CONTINUATION` | hypothesis | `primary_alpha` | enforce valid universe; liquidity-state gate |
| 4 | `DEPTH_REFILL_IMBALANCE` | hypothesis | `primary_alpha` | enforce valid universe |
| 5 | `SPREAD_BLOWOUT_RECOMPRESSION` | hypothesis | `primary_alpha` | enforce valid universe; spread regime |
| 6 | `AGGRESSOR_DECELERATION_FADE` | hypothesis | `primary_alpha` | enforce valid universe |
| 7 | `FORCED_LIQUIDATION_CASCADE` | hypothesis | `primary_alpha` | enforce valid universe; cascade actor spec |
| 8 | `FALSE_BREAKOUT_TRAP` | hypothesis | `primary_alpha` | enforce valid universe |
| 9 | `CANCEL_STORM_BEFORE_MOVE` | hypothesis | `primary_alpha` | enforce valid universe; no spoofing inference overclaim |
| 10 | `QUEUE_DEPLETION_TRIGGER` | hypothesis | `primary_alpha` | enforce valid universe; queue-depth feature availability |
| 11 | `BOOK_SLOPE_COLLAPSE` | hypothesis | `primary_alpha` | enforce valid universe |
| 12 | `ABSORPTION_FADE` | hypothesis | `primary_alpha` | enforce valid universe; toxicity/continuation overlay later |
| 13 | `ICEBERG_RELOAD_DETECTION` | hypothesis | `primary_alpha` | enforce valid universe; detection confidence gate |
| 14 | `LIQUIDITY_DEFENSE_BREAK` | hypothesis | `defensive_overlay` pending spec | current catalog role defensive; do not PnL-rank standalone until spec clarifies directional claim |
| 15 | `ONE_SIDED_ADD_CANCEL_IMBALANCE` | hypothesis | `primary_alpha` | enforce valid universe |
| 16 | `ES_MES_LEAD_LAG` | hypothesis | `cross_asset_primary_alpha` | target `MES`; require real `ES` leader tape; no own-symbol placeholder |
| 17 | `NQ_MNQ_LEAD_LAG` | hypothesis | `cross_asset_primary_alpha` | target `MNQ`; require real `NQ` leader tape |
| 18 | `ES_NQ_DIVERGENCE_SNAPBACK` | hypothesis | `cross_asset_primary_alpha` | require real `ES` and `NQ` context; define target policy explicitly |
| 19 | `ZN_ZB_ES_NQ_MACRO_IMPULSE` | hypothesis | `cross_asset_primary_alpha` | target ES/MES/NQ/MNQ; audit whether required leaders must be `ZN` and `ZB` rather than current `ZN` only |
| 20 | `MICRO_CONTRACT_RETAIL_LAG` | hypothesis | `cross_asset_primary_alpha` | target `MES`; require real `ES` leader tape |
| 21 | `ROUND_NUMBER_STOP_SWEEP` | hypothesis | `primary_alpha` | enforce valid universe |
| 22 | `PRIOR_HIGH_LOW_BREAKOUT_TRAP` | hypothesis | `primary_alpha` | enforce valid universe |
| 23 | `OPENING_CANDLE_CHASE` | hypothesis | `primary_alpha` | enforce session/event clock; valid universe |
| 24 | `VWAP_DEFENSE_BREAK` | hypothesis | `primary_alpha` unless spec reclassifies defensive | enforce VWAP feature authority |
| 25 | `DOM_ILLUSION_TRAP` | hypothesis | `primary_alpha` | enforce valid universe |
| 26 | `LATE_CANDLE_ENTRY_FADE` | hypothesis | `primary_alpha` | enforce event/session clock |
| 27 | `STOP_LOSS_CASCADE_CONTINUATION` | hypothesis | `primary_alpha` | enforce valid universe |
| 28 | `PANIC_MARKET_ORDER_SPREAD_TAX` | hypothesis | `primary_alpha` | cost hurdle critical; spread/fee/slippage evidence |
| 29 | `END_OF_DAY_FORCED_FLATTEN_FLOW` | hypothesis | `defensive_overlay` / scheduled-flow component pending spec | current catalog defensive; avoid standalone PnL bucket until role clarified |
| 30 | `CUTOFF_PANIC_EXITS` | hypothesis | `primary_alpha` | enforce cutoff/session clock |
| 31 | `NO_OVERNIGHT_INVENTORY_SQUEEZE` | hypothesis | `primary_alpha` | enforce overnight/session constraints |
| 32 | `DAILY_LOSS_LIMIT_DEFENSE` | hypothesis | `defensive_overlay` | not standalone alpha unless new spec creates directional rule |
| 33 | `TRAILING_DRAWDOWN_PRESSURE` | hypothesis | `primary_alpha` | prop/risk actor spec; session clock |
| 34 | `PROFIT_LOCK_BEHAVIOR` | hypothesis | `primary_alpha` | actor-flow spec; session clock |
| 35 | `MAX_CONTRACT_CROWDING_IN_MICROS` | hypothesis | `primary_alpha` pending universe audit | name says micros; registry currently broad; reconcile before full campaign |
| 36 | `PROP_RESET_REOPEN_WINDOW` | hypothesis | `primary_alpha` | prop reset clock |
| 37 | `FRIDAY_WEEKEND_DERISKING` | hypothesis | `primary_alpha` | calendar/session clock |
| 38 | `ECONOMIC_EVENT_RESTRICTION_FLATTENING` | hypothesis | `primary_alpha` | event restriction clock |
| 39 | `QUOTE_PULL_BEFORE_VOLATILITY` | hypothesis | `defensive_overlay` / trade veto | current catalog `blocks_trade=true`; never rank as standalone alpha |
| 40 | `REQUOTE_RACE_AFTER_SHOCK` | hypothesis | `defensive_overlay` / execution-risk component | current catalog defensive; no standalone until spec says otherwise |
| 41 | `THIN_BOOK_CONTINUATION` | hypothesis | `primary_alpha` | thin-book and toxicity gates |
| 42 | `PASSIVE_TRAP_FILL` | hypothesis | `primary_alpha` / execution-quality sensitive | fill realism and queue model critical |
| 43 | `REBATE_TRAP_AVOIDANCE` | hypothesis | `execution_quality_overlay` pending spec | name is avoidance; do not blindly treat as directional alpha without spec |
| 44 | `SPREAD_REGIME_CHANGE` | hypothesis | `primary_alpha` / regime component | regime transition sign/horizon spec |
| 45 | `GHOST_ROUTE` | hypothesis | `primary_alpha` | canonical MBO queue-decay/OFI authority |
| 46 | `VIX_SPIKE_EVENT_FADE` | hypothesis | `sensor_conditioned_primary_alpha` | require point-in-time `VIX` sensor tape; else `sensor_tape_missing` |
| 47 | `VIX_QUOTE_PULL_LIQUIDITY_VACUUM` | hypothesis | `sensor_conditioned_primary_alpha` | require `VIX`; quote-pull semantics |
| 48 | `VIX_IMPLIED_REALIZED_GAP` | hypothesis | `sensor_conditioned_primary_alpha` | require `VIX`; implied/realized feature authority |
| 49 | `VIX_DEPTH_IMBALANCE_DIRECTION` | hypothesis | `sensor_conditioned_primary_alpha` | require `VIX`; depth context |
| 50 | `VIX_LEVEL_CONDITIONED_CONTINUATION` | hypothesis | `sensor_conditioned_primary_alpha` | require `VIX`; level conditioning |
| 51 | `BOOK_PRESSURE` | pdf_structural | `context_feature` / structural eval only | parser already avoids making it primary; no generic standalone HBT order loop |
| 52 | `CROSS_ASSET_LEAD_LAG` | pdf_structural | `context_feature` | distinct from HYP 16–20; do not trade generic payload standalone |
| 53 | `VPIN_TOXICITY` | pdf_structural | `defensive_overlay` | widen/reduce/cancel; not standalone alpha |
| 54 | `HYBRID_EXECUTION` | pdf_structural | `execution_engine` | quote engine requiring `BOOK_PRESSURE` + `VPIN_TOXICITY`; separate replay path |
| 55 | `DEALER_HEDGING` | pdf_structural | `options_fixture` / hybrid context | options context/parity fixture; not CME futures standalone alpha by default |
| 56 | `DOW_YM_INDEX` | pdf_structural | `context_feature` / YM target clue | may feed YM/MYM strategy after target spec; not generic order loop |
| 57 | `TREASURY_CTD` | pdf_structural | `context_feature` / rates basis diagnostic | hybrid/diagnostic; not standalone until strategy spec exists |
| 58 | `TRANSFER_ENTROPY` | pdf_structural | `context_feature` / diagnostic | no direct HBT structural signal field currently; no standalone trade |
| 59 | `QUANTUM_SPREAD_DEFENSE` | pdf_structural | `defensive_overlay` / veto | `blocks_trade=true`; never standalone alpha |
| 60 | `STOCHASTIC_THERMO` | pdf_structural | `context_feature` / regime diagnostic | no direct order strategy until spec says sign/horizon |
| 61 | `HAWKES_TOXIC_FLOW` | pdf_structural | `defensive_overlay` | toxic cascade/reservation skew gates execution; never `reservation_price_skew -> BUY/SELL` standalone |
| 62 | `RL_EXECUTION_POLICY` | reinforcement_learning | `rl_research_blocked` | emit `pipeline_blocker:missing_uniform_hbt_adapter` or simulator blocker |
| 63 | `RL_DEEP_Q_EXECUTION_PROXY` | reinforcement_learning | `rl_research_blocked` | research/proxy only until real replay interaction adapter exists |
| 64 | `RL_VIX_OPTIONS_CLUE_PROXY` | reinforcement_learning | `rl_research_blocked` | VIX.OPT clue lane; not futures HBT target |
| 65 | `RL_PPO_SIM_POLICY` | reinforcement_learning | `rl_research_blocked` | simulator trajectory blocker until real adapter exists |

## 6. Implementation phases

### Phase 0 — Decision correction before code

Create a vault/repo decision note that explicitly refines the 2026-06-29 all-model rule:

- Preserve all-slug ledger coverage.
- Supersede the clause that semantic roles may not route active units.
- Define `no-cherry-pick v2` as coverage without forced standalone execution.
- Record that existing flattened Stage C/Pass A economic conclusions are valid only for the old flattened interpretation, not as model-worth conclusions.

Update affected docs:

- `docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md`
- `docs/project/HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md`
- `docs/project/VBT_MODEL_ONTOLOGY.md` if needed to distinguish historical VectorBT ontology from active HBT semantic routing.
- Vault `wiki/money-path.md` only if status/pointers change after verified implementation.

### Phase 1 — Central contract layer

Add one central semantic contract source used by manifest builder, runner, workbench/router tests, and future campaign summaries.

Candidate file:

```text
packages/backtest_pipeline/src/model_execution_contracts.py
```

Minimum implementation:

- Load all slugs from `model_registry.yaml`.
- Merge existing role/dependency metadata from `apps/workbench/config/model_catalog.yaml` (this is where `blocks_trade` lives — it is not in the registry) and `apps/workbench/config/models.yaml`.
- Absorb the `pipeline_model_router.py` role frozensets (section 4) rather than re-deriving PDF roles.
- Pull `valid_instrument_universe` from registry (present on all 65 slugs). `target_instrument_universe` is sparse — only 4 models carry it (`ES_MES_LEAD_LAG`, `NQ_MNQ_LEAD_LAG`, `ZN_ZB_ES_NQ_MACRO_IMPULSE`, `MICRO_CONTRACT_RETAIL_LAG`). Treat its absence as "no target constraint," not an error.
- Reuse the existing `required_leaders_for_model()` and `required_sensors_for_model()` from `replay.cross_asset_assembly` — both already exist, and `REQUIRED_SENSORS_BY_MODEL` already lists the five VIX models. Do not re-add the requirement tables; only the enforcement/validation is missing (see Phase 4).
- Emit fail-closed `unknown_semantic_contract` for any slug not covered.

No hidden allowlist that silently drops models. Tests must assert exact coverage equals `all_slugs()`.

### Phase 2 — Manifest semantic admissibility

Update `packages/backtest_pipeline/src/hftbacktest_only_campaign_manifest.py`:

- Add semantic columns to every row.
- Emit all 65 slugs in inventory/ledger surfaces.
- Only rows with `standalone_hbt_policy in {standalone_executable, requires_leader_tape satisfied, requires_sensor_tape satisfied}` become HBT order queue candidates.
- Composition-only / diagnostic / execution-engine / RL rows remain in manifest with blockers, not omitted.
- Enforce symbol/contract compatibility:
  - if `target_instrument_universe` exists and row symbol is not target-compatible: `semantic_blocker:target_instrument_mismatch`.
  - if `valid_instrument_universe` exists and row symbol is outside it: `semantic_blocker:invalid_instrument_for_model`.
  - if required leader missing: `data_blocker:leader_tape_missing`.
  - if required sensor missing: `data_blocker:sensor_tape_missing`.
  - if model is composition-only: `semantic_blocker:composition_only_not_standalone`.
  - if model blocks trade: `semantic_blocker:defensive_veto_not_standalone`.
  - if RL lacks adapter: `pipeline_blocker:missing_uniform_hbt_adapter`.

Critical regression: missing leader/sensor/target mismatch must not appear as `signal_below_threshold` or `no_hbt_order_submitted`.

### Phase 3 — Runner fail-closed guard

Update `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py` and `scripts/run_hftbacktest_only_campaign.py`:

- Refuse to run `hypothesis_limit_order` for rows whose semantic contract is not standalone executable.
- Remove or guard generic structural BUY/SELL conversion for defensive/context models.
- Specifically prevent:

```text
HAWKES_TOXIC_FLOW.reservation_price_skew -> standalone BUY/SELL
VPIN_TOXICITY -> standalone BUY/SELL
QUANTUM_SPREAD_DEFENSE -> standalone BUY/SELL
HYBRID_EXECUTION -> hypothesis_limit_order
BOOK_PRESSURE -> hypothesis_limit_order unless an explicit primary strategy spec wraps it
```

- Runner receipts must distinguish:
  - `semantic_blocker:*`
  - `data_blocker:leader_tape_missing`
  - `data_blocker:sensor_tape_missing`
  - `signal_below_threshold` only when all semantic/data requirements are satisfied and the primary signal is truly below threshold.

### Phase 4 — Cross-asset and sensor binding

Update `packages/replay/cross_asset_assembly.py` or adjacent contract module:

- Keep existing tests for real leader vs own-symbol placeholder (`tests/test_cross_asset_assembly.py`: `test_own_symbol_placeholder_detected`, `test_validate_accepts_real_leader_leg`, `test_validate_rejects_missing_leader_leg`).
- Add a target-universe enforcement helper. `default_leaders_for_target()` already exists for target->leader defaults; the missing piece is a validator that rejects a row whose symbol is outside `target_instrument_universe`, parallel to `validate_cross_asset_alignment` on the leader side.
- Add a VIX sensor *enforcement* helper only. The requirement data already exists — `REQUIRED_SENSORS_BY_MODEL` (five VIX models) and `required_sensors_for_model()` — but no validator fails a row when the VIX sensor tape is absent. Add the validator, not the requirement table.
- Audit `ZN_ZB_ES_NQ_MACRO_IMPULSE`: the leader dict `REQUIRED_LEADERS_BY_MODEL` requires only `("ZN",)`, while the registry `valid_instrument_universe` already lists `ZN` and `ZB` (plus ES/MES/NQ/MNQ). Decide from spec/code whether the leader dict should add `ZB` (required), keep it optional, or treat `ZB` as a separate clue. This edit is to the leader dict, not the registry universe.
- Audit `MAX_CONTRACT_CROWDING_IN_MICROS`: name says micros, registry `valid_instrument_universe` is broad (ES/MES/NQ/MNQ/YM/MYM/RTY/M2K/CL/MCL/NG/GC/MGC/SI/HG/ZN/ZB/ZF/ZT); decide whether the valid universe should be micro-only or the name/description should change.

### Phase 5 — Parameter-surface policy

Update parameter-surface expansion so non-standalone rows are not assigned fake directional strategy params:

- `primary_alpha` rows may carry `signal_threshold`, `holding_period_bars`, stops/takes, etc.
- `defensive_overlay` rows carry overlay/gating params only when part of a declared composition.
- `execution_engine` rows carry quote-engine params under their own runner, not `hypothesis_limit_order` params.
- `context_feature` rows carry diagnostic/eval params only.
- `rl_research_blocked` rows carry adapter/simulator blocker receipts.

Parameter proposals with `objective_evaluations=0` remain declarations, not optimization evidence.

### Phase 6 — Tests first / regression coverage

Add or update tests before changing production behavior where possible.

Required test groups:

1. **Catalog coverage**
   - every slug in `all_slugs()` has a `ModelExecutionContract`;
   - test count derives from the registry loader (`len(all_slugs()) == 65` computed, not a literal). A code-verification pass found no stale hard-coded count in `tests/backtest_pipeline/test_hftbacktest_only_campaign_manifest.py`; if any test elsewhere (router/workbench) hard-codes a model count, locate that actual site and switch it to derive from the registry — do not assume `56` exists;
   - no unknown semantic policy.

2. **PDF/structural non-standalone guard**
   - HAWKES cannot become standalone order signal from `reservation_price_skew`;
   - VPIN/Quantum/Hybrid cannot become `hypothesis_limit_order` rows;
   - BookPressure is context/feature unless wrapped by a primary hypothesis/spec.

3. **Defensive hypothesis guard**
   - `QUOTE_PULL_BEFORE_VOLATILITY` with `blocks_trade=true` emits defensive/veto blocker when standalone;
   - `DAILY_LOSS_LIMIT_DEFENSE`, `END_OF_DAY_FORCED_FLATTEN_FLOW`, `LIQUIDITY_DEFENSE_BREAK`, `REQUOTE_RACE_AFTER_SHOCK` are not silently PnL-ranked as primary alpha until specs say otherwise.

4. **Target universe guard**
   - `ES_MES_LEAD_LAG` on ES target fails `target_instrument_mismatch`;
   - `ES_MES_LEAD_LAG` on MES without ES leader fails `leader_tape_missing`;
   - compatible MES + ES leader is runnable.

5. **Sensor guard**
   - VIX models without VIX sensor fail `sensor_tape_missing`;
   - missing VIX never reports `signal_below_threshold`.

6. **Runner receipt honesty**
   - semantic blockers do not create recorder/stats/promotion artifacts;
   - semantic blockers are not model rejections;
   - `signal_below_threshold` only occurs after semantic/data admissibility passes.

7. **Canary summary semantics**
   - summary separates `semantic_blocker`, `data_blocker`, `pipeline_blocker`, `hbt_completed`, `signal_below_threshold`.

Candidate focused commands:

```bash
python -m pytest -q \
  tests/backtest_pipeline/test_pipeline_model_router.py \
  tests/backtest_pipeline/test_hftbacktest_only_campaign_manifest.py \
  tests/backtest_pipeline/test_hftbacktest_only_pipeline.py \
  tests/test_cross_asset_assembly.py \
  tests/test_research_pipeline.py \
  tests/test_workbench/test_model_catalog.py \
  tests/test_workbench/test_registry.py
```

Final scope after implementation:

```bash
python -m pytest -q tests/backtest_pipeline tests/test_cross_asset_assembly.py tests/test_research_pipeline.py tests/test_workbench
```

If scope remains ambiguous, run full repo pytest or `scripts/run_agent_verify.ps1` per AGENTS.md.

### Phase 7 — Review and GrepLoop

Before claiming completion:

- Run local preflight search for stale language:
  - `all-model uniform-flow` used as forced standalone execution;
  - `kind must not route` without no-cherry-pick v2 caveat;
  - `signal_below_threshold` masking leader/sensor/semantic blockers;
  - `pdf_structural` marked executable via adapter only;
  - `reservation_price_skew` used as direct trade signal.
- Run reviewer dual-pass:
  - Pass A: code simplicity / contract correctness.
  - Pass B: math/market invariant review — no lookahead, event-time, target/leader alignment, cost-hurdle honesty.
- Run external PR AI review / GrepLoop once a review surface exists.
- Do not say merge-ready without 0 red reviewer findings and green verification output.

## 7. Acceptance criteria

The fix is complete only when all are true:

1. Every one of the 65 registry slugs has a semantic execution contract.
2. Manifest emits coverage for every slug without omission.
3. Only standalone-executable rows enter `hypothesis_limit_order` HBT order runs.
4. Non-standalone models produce honest semantic/composition/diagnostic/RL blocker receipts.
5. Target instrument universe is enforced in HBT manifest/runner path.
6. Required leader and sensor tapes are enforced before signal evaluation.
7. Missing context cannot be counted as `signal_below_threshold`.
8. Defensive/context/PDF/RL rows are not counted as economic failures or evidence of no alpha.
9. Existing flattened Stage C results are labeled as old-semantics evidence, not whole-catalog model-worth evidence.
10. Focused tests and final scope tests pass with captured output.

## 8. Non-goals for this fix

- Do not reintroduce VectorBT / Stage-A / robustness as pre-HBT eligibility selectors.
- Do not hand-pick symbols or models to improve results.
- Do not invent new model math.
- Do not promote any model economically during the semantic-routing repair.
- Do not run paid Stage C again until semantic routing and blocker accounting are green.
- Do not route live/paper traffic; this is offline HBT research plumbing only.

## 9. Immediate next actions

1. Write the superseding/refining decision note for no-cherry-pick v2.
2. Add contract tests for all 65 slugs and the blocked/non-standalone examples.
3. Implement the central contract layer with existing catalog/registry inputs.
4. Wire manifest builder semantic admissibility.
5. Wire runner fail-closed guard.
6. Update cross-asset/sensor target helpers.
7. Run focused tests.
8. Reviewer dual-pass.
9. Full verification.
10. Only then consider a new canary manifest/run.
