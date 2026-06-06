# Workbench Production Readiness Checklist

Operational checklist for turning the Developer Work Order into production-ready workbench behavior. Keep this as Markdown, not YAML. Do not mark broad items complete without concrete evidence.

## Current Completed Items

- [x] Deterministic replay future-event buffering fix completed.
  - Evidence: `packages/replay/market_data_adapter.py` updated.
  - Evidence: `tests/test_historical_replay_market_data_adapter.py` added.
  - Evidence: no-graph targeted verification reported `8 passed in 4.23s`.
  - Evidence: reviewer receipt reported 0 red, 1 yellow due untracked test file.
- [x] Workbench history/data gate fail-closed behavior completed.
  - Evidence: `apps/workbench/src/run/engine.py` updated.
  - Evidence: `tests/test_workbench/test_engine_history_gate.py` added.
  - Evidence: `skip_history_gate=True` can complete but does not force `data_sufficient` true and cannot promote insufficient data.
  - Evidence: `skip_history_gate=False` raises a data insufficiency `RuntimeError`.
  - Evidence: reviewer receipt for current batch reported 0 red, 1 yellow due untracked new files.
  - Evidence: no-graph verification reported `14 passed, 4 warnings in 5.39s` using replay plus workbench history gate/matrix tests.
- [x] Selected missing workbench campaign artifact blocker completed.
  - Evidence: `apps/workbench/src/run/evidence_snapshot.py` updated.
  - Evidence: `tests/test_workbench/test_evidence_snapshot.py` updated.
  - Evidence: selected `campaign_id` whose artifact directory is missing now returns `state="blocked"`, `current_stage="campaign_artifact_missing"`, `decision.action="BLOCKED"`, and a blocking gate instead of idle.
  - Evidence: reviewer receipt for expanded batch reported 0 red, 0 yellow.
  - Evidence: no-graph shell verification reported `64 passed, 4 warnings in 9.80s` using replay plus workbench history gate/matrix plus evidence snapshot tests.
- [x] Certification full-suite failure semantics completed.
  - Evidence: `packages/hft3/validation/certification_runner.py` updated.
  - Evidence: `tests/test_backtester_certification_governance.py` updated.
  - Evidence: blocking certification failures, including T2 full-suite failure with `failed_count <= 2`, force RED.
  - Evidence: lane failures without blocking may remain YELLOW.
  - Evidence: reviewer receipt for certification patch reported 0 red, 0 yellow; reviewer merge-ready yes for certification patch, repo merge-ready no due broader scope.
  - Evidence: no-graph shell verification reported `92 passed, 4 warnings in 11.47s` across replay/workbench/certification slices.
- [x] L3 data-quality gating completed.
  - Evidence: `apps/workbench/src/data/manifest.py` updated.
  - Evidence: `apps/workbench/src/run/engine.py` updated.
  - Evidence: `tests/test_workbench/test_l3_loader.py` updated.
  - Evidence: `tests/test_workbench/test_engine_history_gate.py` updated.
  - Evidence: monotonic timestamp violations and duplicate ADD order IDs produce stable `DATA_QUALITY` blockers.
  - Evidence: L3 data-quality blockers keep `data_sufficient` false and prevent promotion even with `skip_history_gate=True`.
  - Evidence: reviewer receipt for reviewed L3 scope reported 0 red, 0 yellow; scope-green yes.
  - Evidence: no-graph shell verification reported `98 passed, 4 warnings in 13.56s` across accumulated replay/workbench/certification slices.
- [x] CME config fail-closed/source-of-truth loading completed.
  - Evidence: `packages/hft3/validation/lanes/adapters/cme_adapter.py` updated.
  - Evidence: `tests/test_hft3_validation/test_lane_adapters.py` updated.
  - Evidence: `load_cme_config` raises `CMEConfigError` on missing, empty, missing-column, empty-value, and non-numeric `events.csv`.
  - Evidence: valid CSV still loads `WindowConfig`.
  - Evidence: explicit `CMEConfig()` defaults remain intentional.
  - Evidence: reviewer receipt for CME config diff reported 0 red, 0 yellow; scope-green yes.
  - Evidence: no-graph shell verification reported `121 passed, 4 warnings in 10.86s` across accumulated checklist slices.
- [x] Lane scorecard config-loader exception handling completed.
  - Evidence: `packages/hft3/validation/lanes/scorecard.py` updated.
  - Evidence: `tests/test_hft3_validation/test_unified_scorecard.py` updated.
  - Evidence: config-loader exceptions create blocking `LaneCoverage` with `CONFIG_LOAD_FAILED`, `failure_reasons`, `config_loader_error`, and preserved `test_paths`.
  - Evidence: config-loader returning `None` remains non-error empty coverage.
  - Evidence: reviewer receipt for scorecard scope reported 0 red, 0 yellow; reviewer merge-ready yes.
  - Evidence: no-graph shell verification reported `154 passed, 4 warnings in 11.07s` across accumulated checklist slices.
- [x] Workbench tab navigation truthfulness completed.
  - Evidence: `apps/workbench/ui/flow_state.py` updated.
  - Evidence: `apps/workbench/ui/app.py` remains compatible with `streamlit>=1.33` via plain `st.tabs(WORKFLOW_TABS)`.
  - Evidence: `tests/test_workbench/test_flow_state.py` updated.
  - Evidence: `tests/test_workbench/test_ui_imports.py` updated.
  - Evidence: `navigate_to_tab` records a non-widget requested tab/hint, does not mutate `wb_ui_tab`, and the hint truthfully says to review the tab when ready rather than claiming automatic navigation.
  - Evidence: reviewer receipt after correction reported 0 red, 0 yellow; scope-green yes.
  - Evidence: no-graph verification reported `187 passed, 6 warnings in 14.71s` across accumulated checklist slices.
- [x] WFC missing-bounds blocking gate completed.
  - Evidence: `apps/workbench/src/run/campaign_runner.py` updated.
  - Evidence: `tests/test_workbench/test_wfc_campaign_integration.py` updated.
  - Evidence: enabled non-trial WFC missing required parameter bounds records `SKIPPED_MISSING_PARAMETER_BOUNDS`.
  - Evidence: required/missing-bounds evidence is set, `promote_candidate` is blocked, and a `walk_forward_correlation` blocking gate is appended.
  - Evidence: evaluated WFC behavior with bounds remains unchanged.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; scope-green yes for WFC integration scope.
  - Evidence: no-graph shell verification reported `204 passed, 16 warnings in 24.43s` across accumulated checklist slices.
- [x] CMEBacktester replay execution evidence fail-closed behavior completed.
  - Evidence: `packages/hft3/validation/lanes/adapters/cme_adapter.py` updated.
  - Evidence: `tests/test_hft3_validation/test_lane_adapters.py` updated.
  - Evidence: `CMEBacktester.run()` no longer returns clean structural-zero metrics when target replay evidence is missing.
  - Evidence: missing target replay artifacts now return `degraded=True` with `execution_evidence_status="MISSING_REPLAY_ARTIFACT"`.
  - Evidence: real metrics are loaded only from `engines.replay_execution_adapter.result` in `research_cards/{target}_replay*/result.json`.
  - Evidence: skipped, errored, malformed, missing-engine, missing-result, missing-metric, or invalid-metric replay artifacts degrade instead of synthesizing execution evidence.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for the scoped adapter diff.
  - Evidence: no-graph shell verification reported `77 passed in 61.97s` for the focused CME validation/certification slice.
- [x] CME runtime readiness contract fail-closed behavior completed.
  - Evidence: `apps/workbench/src/run/evidence_snapshot.py` updated.
  - Evidence: `tests/test_workbench/test_evidence_snapshot.py` updated.
  - Evidence: CME evidence snapshots now include additive `runtime_readiness` in both `decision` and `system` with schema `workbench_cme_runtime_readiness_v1`.
  - Evidence: CME `decision.live_registry_ready` is derived from runtime readiness, not scattered optimistic booleans.
  - Evidence: Rithmic endpoint status exceptions return a blocking readiness artifact instead of crashing the snapshot.
  - Evidence: a ready endpoint without submit-to-ack evidence remains `BLOCKING`.
  - Evidence: all-lanes summary blockers force `live_registry_ready=False` and downgrade stale `PROMOTE` decisions to `BLOCKED`.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for the scoped runtime-readiness diff.
  - Evidence: no-graph shell verification reported `53 passed in 7.39s` for `tests/test_workbench/test_evidence_snapshot.py`.
- [x] Workbench shared tab header blocker surfacing completed.
  - Evidence: `apps/workbench/ui/evidence_panels.py` updated.
  - Evidence: `tests/test_workbench/test_ui_imports.py` updated.
  - Evidence: every tab using `render_run_header` now emits a visible `st.error` banner when backend decision evidence has blocking gates.
  - Evidence: blocked backend decisions with `live_registry_ready=False` surface the backend reason/current stage instead of appearing as metric-only headers.
  - Evidence: quarantine-only states do not create a blocker banner unless the backend provides explicit blocking gates.
  - Evidence: ready snapshots with no blockers stay quiet.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for the scoped UI diff.
  - Evidence: no-graph shell verification reported `29 passed, 2 warnings in 5.53s` for `tests/test_workbench/test_ui_imports.py`.
  - Evidence: combined backend/UI contract verification reported `82 passed, 2 warnings in 15.23s`.
- [x] Workbench catalog manifest-only timeout fixed.
  - Evidence: `apps/workbench/scripts/backfill_catalog.py` updated.
  - Evidence: `tests/test_workbench/test_catalog_manifest.py` updated.
  - Evidence: manifest-only catalog backfill no longer estimates Databento download cost unless `--download-missing` or `--max-cost-usd` is requested.
  - Evidence: manifest-only output records `cost_estimate_status="not_requested_manifest_only"` and remains local/deterministic.
  - Evidence: reviewer confirmed download and cost-capped paths still call the estimator.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for the scoped catalog diff.
  - Evidence: no-graph shell verification reported `2 passed, 8 warnings in 37.54s` for `tests/test_workbench/test_catalog_manifest.py`.
  - Evidence: broader catalog group verification reported `7 passed, 9 warnings in 48.79s`.
- [x] CME canonical replay evidence path contract drift fixed.
  - Evidence: `scripts/run_event_replay.py` now resolves its default `events.csv` through `hft3_bootstrap.data_system_root(_REPO) / "config" / "events.csv"`.
  - Evidence: default replay outputs now write under the shared workbench `artifact_root()` unless `--out` is explicitly provided.
  - Evidence: `CMEBacktester` now searches the same shared artifact root for `{target}_replay*/result.json` evidence instead of hard-coding legacy `research_cards/`.
  - Evidence: missing evidence still degrades with `MISSING_REPLAY_ARTIFACT`; invalid evidence still degrades instead of manufacturing metrics.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for this scoped CME evidence-path patch only.
  - Evidence: no-graph focused verification reported `31 passed in 4.84s` for `tests/test_run_event_replay.py tests/test_hft3_validation/test_lane_adapters.py`.
- [x] Runtime MODEL_CARD and VALIDATION_CARD artifact emission completed.
  - Evidence: `apps/workbench/src/run/campaign_runner.py` now writes additive `model_card.json` and `validation_card.json` during existing campaign finalization.
  - Evidence: cards are emitted after institutional metrics and blocking gates are attached, so blocked or incomplete validation remains `research_only` or `rejected` rather than production eligible.
  - Evidence: summary artifacts link to the emitted cards through `model_card_path` and `validation_card_path`.
  - Evidence: generated cards validate against `docs/schemas/MODEL_CARD.schema.json` and `docs/schemas/VALIDATION_CARD.schema.json` with zero schema errors.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for this scoped card-emission patch only.
  - Evidence: no-graph focused verification reported `6 passed, 9 warnings in 12.30s` for `tests/test_workbench/test_wfc_campaign_integration.py`.
- [x] Production promotion records now require model-card and validation-card provenance.
  - Evidence: `PromotionRecord` now carries `model_card_path` and `validation_card_path`.
  - Evidence: `save_promotion` fail-closes only for `PROMOTED` records when card paths are missing, outside the registry root, missing on disk, malformed, unwrapped, missing IDs, or mismatched.
  - Evidence: `REJECTED` and `QUARANTINED` records remain writable without cards, avoiding robustness-test friction for non-production outcomes.
  - Evidence: Trade Manager phase fixtures now attach minimal wrapped card provenance to synthetic `PROMOTED` records.
  - Evidence: reviewer receipt reported 0 red, 0 yellow; merge-ready yes for this scoped promotion-provenance patch only.
  - Evidence: no-graph focused verification reported `130 passed in 2.00s` for promotion-record plus Trade Manager phase 14-19 tests.

## Current Blockers

- [ ] CME production readiness is blocked until runtime gates, acceptance criteria, and scope verification are green.
- [ ] Broader workbench production readiness remains blocked until all runtime gates have fail-closed evidence and blocked states prevent production claims.
- [ ] Frontend/backend runtime contracts are missing or too loose for production readiness.
- [ ] Workbench has not produced production-ready CME models with validated alpha/edge.
  - Audit evidence: all-lanes runtime summaries are `state="planned"` and `decision_action="BLOCKED"`.
  - Audit evidence: latest parsed all-lanes runs have `EXECUTED=0`, `PROMOTED=0`, and blocker `model_execution: No model backtest/replay evidence has been emitted for this active run`.
  - Audit evidence: no `promote_candidate=true`, `production_eligible`, `robustness_passed=true`, `expected_edge`, `edge_bps`, model-card, or validation-card runtime artifact was found for CME production readiness.
  - Audit evidence: repo audit found sampled PASS workbench summaries still have `promote_candidate=false`, `pending_CHI404` simulation shadow, or failed/missing latency envelope evidence.
- [ ] Full promotion source-lineage completeness remains incomplete.
  - Audit evidence: production promotions now require model-card and validation-card provenance, but broader source lineage fields such as dataset, feature set, config hash, and run manifest are not yet cross-validated against the emitted cards and campaign artifacts.
  - Unblock condition: cross-check promotion record identity and lineage fields against the campaign summary/cards/manifest before any production promotion claim.
- [ ] Current canonical CME replay execution artifact is still missing.
  - Audit evidence: path contract drift is fixed, but no fresh canonical `engines.replay_execution_adapter.result` card has been produced by the existing CME replay flow for the readiness claim.
  - Audit evidence: observed CPI replay evidence uses older engine keys, so it remains invalid for `CMEBacktester` production-readiness evidence.
  - Attempt evidence: `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT` exited with missing measured paper order submit-to-ack latency, as expected from the existing latency gate.
  - Attempt evidence: `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --latency-ms 1.0` exceeded the 5-minute hard stop and was killed; no fresh replay artifact was written.
  - Attempt evidence: `CMEBacktester(CMEConfig()).run(target="CPI_2024_09_11_TIGHT")` degrades the stale artifact with `INVALID_REPLAY_ARTIFACT` because `engines.replay_execution_adapter` is absent.
  - Unblock condition: run the existing canonical CME replay entrypoint and attach the current artifact path plus green command output to this checklist.
- [ ] CME latency and execution acceptance evidence is incomplete.
  - Audit evidence: runtime latency report marks Rithmic app/e2e submit-to-ack evidence blocked.
  - Audit evidence: CHI404 latency/sim-shadow evidence is pending or failing in sampled Workbench runs.
  - Audit evidence: simulated exchange fill attribution is not production-realistic enough to support production edge claims without additional per-order fill mapping evidence.
- [ ] CME MBO production ingestion and event-time correctness remain incomplete.
  - Audit evidence: NPZ feed uses `local_ts` as `timestamp_ns` and does not require exchange timestamp, sequence, or provenance metadata.
  - Audit evidence: Rithmic trial conversion remains trade/BBO-limited and explicitly does not map full MBO depth.
  - Audit evidence: order-book reconstruction has checks, but Workbench gap handling can mark snapshots available without actual snapshot ingestion evidence.
- [x] Relevant non-crypto evidence files are staged, but not committed and not production-ready.
  - Evidence: `git add` staged the non-crypto checklist hardening paths.
  - Evidence: `git diff --cached --name-only` showed the requested non-crypto paths.
  - Evidence: tracked unstaged diff is limited to generated runtime validation artifacts after verification.
  - Evidence: staged paths include replay, workbench backend/UI, CME validation adapter, scorecard, catalog manifest, targeted tests, and `docs/workbench/PRODUCTION_READINESS_CHECKLIST.md`.
- [x] Broad no-graph scope verification completed.
  - Blocker evidence: broader no-graph scope verification was attempted with `python -m pytest tests/backtester_validation/fast tests/test_workbench tests/test_hft3_validation tests/test_data_layer tests/test_mbo_agent_schemas.py tests/test_gate_schema.py -q --tb=short --ignore=tests/test_workbench/test_catalog_event_e2e.py`.
  - Blocker evidence: command exited `124` after timing out at about 604s; the process was killed by hard timeout and no useful pytest output was captured.
  - Resolution: after the manifest-only cost-estimation timeout fix, the same no-graph scope completed successfully.
  - Evidence: `python -m pytest tests/backtester_validation/fast tests/test_workbench tests/test_hft3_validation tests/test_data_layer tests/test_mbo_agent_schemas.py tests/test_gate_schema.py -q --tb=short --ignore=tests/test_workbench/test_catalog_event_e2e.py` reported `566 passed, 1 skipped, 77 warnings in 122.16s`.
- [ ] CME runtime replay execution evidence is not yet production-complete, so CME production readiness remains blocked.
  - Blocker evidence: `CMEBacktester` now fails closed for missing or invalid replay execution evidence.
  - Blocker evidence: current replay execution attempt did not produce a fresh `replay_execution_adapter` artifact within the bounded local run.
  - Blocker evidence: production readiness still requires canonical replay artifacts, acceptance thresholds, runtime gate output, exit codes, and artifact paths from the production CME entrypoints.
  - Unblock condition: run the canonical CME replay/validation flow and attach green command output plus artifact paths to this checklist.
- [ ] Unrelated crypto files remain unstaged, untouched, and out of scope; do not include them in this setup flow: `scripts/backfill_crypto_l3_from_manifest.py`, `tests/test_crypto_l2/test_crypto_l3_backfill_driver.py`.

## Workbench Tab Sync

- [ ] Inventory all workbench tabs and their backing backend endpoints or data sources.
- [ ] Ensure every tab exposes the same readiness state vocabulary: unavailable, blocked, ready, running, passed, failed.
- [ ] Ensure tab labels, disabled states, and status banners reflect backend truth rather than local UI assumptions.
- [ ] Add evidence links or artifact paths for each tab-level readiness claim.
- [ ] Confirm stale tab state is cleared when model, event, run, or environment context changes.

## Frontend/Backend Runtime Contract

- [ ] Define the backend response schema for workbench runtime readiness, run launch, run status, and artifact discovery.
- [ ] Make missing fields, unknown enum values, and backend errors fail closed in the frontend.
- [ ] Validate that frontend controls cannot launch production-style workflows when backend readiness is blocked.
- [ ] Document required fields, optional fields, defaults, and failure behavior.
- [ ] Add contract tests covering happy path, blocked path, missing fields, and stale run state.

## Repo Cleanliness And Chronology

- [ ] Record the current branch and relevant commit chronology before claiming readiness.
- [x] Ensure new tests and docs are tracked when they are part of the evidence chain.
  - Evidence: non-crypto evidence files are staged for the current checklist hardening chain.
- [x] Separate unrelated modified files from the production readiness change set.
  - Evidence: unrelated crypto files remain unstaged, untouched, and out of scope.
- [ ] Confirm generated artifacts are either intentionally tracked or intentionally ignored.
- [x] Preserve chronology in runbooks: what changed, when it was verified, and which evidence supports it.
  - Evidence: Current Completed Items records the sequence of runtime/readiness changes, reviewed scope, and no-graph verification summaries through `204 passed, 16 warnings in 24.43s`.

## CME Lane Readiness

- [ ] Bind CME readiness to the canonical CME replay and validation entrypoints.
- [ ] Confirm no workstation live/paper execution path is introduced.
- [ ] Confirm CME artifacts stay in their intended research/output locations.
- [ ] Verify latency, replay, and acceptance gates are explicitly represented in the workbench.
- [ ] Require blocked status when CME inputs, artifacts, or validation reports are missing.

## Blocker Policy

- [ ] Define blocker severity levels and which severities block readiness claims.
- [ ] Treat missing tests, missing contracts, stale artifacts, and unknown runtime state as blockers.
- [ ] Require every blocker to have owner, unblock condition, and evidence needed for closure.
- [ ] Surface blockers in the UI and docs with the same wording.
- [ ] Keep merge-ready status false while any production blocker remains open.

## LLM Governance

- [ ] Document where LLM assistance is allowed in the workbench workflow.
- [ ] Require deterministic evidence for production readiness claims; LLM summaries alone are not evidence.
- [ ] Record prompts, model outputs, and human review where LLM-generated decisions affect docs or checklist state.
- [ ] Prevent LLM output from overriding acceptance gates, source-of-truth data, or validation failures.
- [ ] Define review requirements for LLM-authored operational docs and runbooks.

## Deterministic/Probabilistic Boundary

- [ ] Separate deterministic replay, validation, and acceptance gates from probabilistic research scoring.
- [ ] Label probabilistic outputs as research signals, not production readiness evidence.
- [ ] Require deterministic reproducibility for replay state, feature inputs, and acceptance reports.
- [ ] Document where random seeds, sampling, or stochastic models enter the pipeline.
- [ ] Ensure readiness gates do not pass based on probabilistic confidence alone.

## Source-Of-Truth Binding

- [ ] Identify the authoritative source for model inventory, event definitions, replay inputs, and validation artifacts.
- [ ] Ensure UI and backend read from the same source of truth for readiness status.
- [ ] Reject or block runs when artifact provenance is missing, stale, or mismatched.
- [ ] Document how source-of-truth changes are reviewed and versioned.
- [ ] Add checks that prevent ad hoc local files from masquerading as canonical evidence.

## Mathematical And Financial Integrity

- [ ] Define invariants for event ordering, future-event buffering, timestamps, latency, and replay determinism.
- [ ] Define financial invariants for PnL, fees, slippage, fills, position state, and risk limits where applicable.
- [ ] Require tests for boundary cases: empty streams, late events, duplicate events, and out-of-order inputs.
- [ ] Ensure acceptance reports include enough numeric detail to audit pass/fail outcomes.
- [ ] Require reviewer audit for changes affecting math, market microstructure assumptions, or financial accounting.

## Pipeline State Machine

- [ ] Define pipeline states from input discovery through replay, validation, artifact publication, and readiness decision.
- [ ] Make illegal transitions fail closed and produce an actionable blocker.
- [ ] Ensure retry, cancellation, partial artifact, and stale artifact states are explicit.
- [ ] Persist enough state to explain the latest readiness decision.
- [ ] Add tests for state transitions and blocked-state propagation.

## CME Acceptance Criteria

- [ ] Define required CME replay inputs, event IDs, configurations, and output artifacts.
- [ ] Define pass/fail thresholds for replay correctness, latency, feature integrity, and report completeness.
- [ ] Require acceptance evidence to include command, exit code, summary output, and artifact paths.
- [ ] Confirm acceptance criteria are visible in docs and enforced by runtime gates.
- [ ] Keep CME production readiness blocked until all acceptance criteria are satisfied.

## Testing Requirements

- [x] Add targeted tests for each changed runtime contract and readiness gate.
  - Evidence: Current Completed Items records added or updated targeted tests for the completed replay, workbench history gate, evidence snapshot, certification, L3 quality, CME config, scorecard, tab truthfulness, and WFC missing-bounds gates.
- [x] Add regression tests for deterministic replay future-event buffering and ordering behavior.
  - Evidence: `tests/test_historical_replay_market_data_adapter.py` was added for deterministic replay future-event buffering behavior.
- [x] Run full no-graph scope verification before any merge-ready claim.
  - Blocker: earlier broad no-graph scope command `python -m pytest tests/backtester_validation/fast tests/test_workbench tests/test_hft3_validation tests/test_data_layer tests/test_mbo_agent_schemas.py tests/test_gate_schema.py -q --tb=short --ignore=tests/test_workbench/test_catalog_event_e2e.py` timed out with exit code `124` after about 604s and produced no useful pytest output.
  - Blocker narrowed: `tests/test_workbench/test_catalog_manifest.py::test_manifest_event_windows_single_year` timed out at 120s before the manifest-only cost-estimation fix.
  - Evidence: rerun after the catalog fix reported `566 passed, 1 skipped, 77 warnings in 122.16s`.
- [x] Record exact commands, exit codes, and output summaries in the handoff.
  - Evidence: no-graph verification summaries are recorded in Current Completed Items, including the accumulated result `204 passed, 16 warnings in 24.43s`.
  - Evidence: focused CME validation/certification verification reported `77 passed in 61.97s`.
  - Evidence: focused runtime readiness verification reported `53 passed in 7.39s`.
  - Evidence: combined backend/UI contract verification reported `82 passed, 2 warnings in 15.23s`.
  - Evidence: catalog manifest verification reported `2 passed, 8 warnings in 37.54s`.
  - Evidence: broad no-graph scope verification reported `566 passed, 1 skipped, 77 warnings in 122.16s`.
- [ ] Document skipped tests with blocker, reason, and unblock condition.

## Docs And Runbook Requirements

- [ ] Provide an operator runbook for checking workbench readiness from a clean checkout.
- [ ] Document how to interpret blocked, failed, and ready states.
  - Partial: this checklist consistently treats missing contracts, stale artifacts, failed gates, skipped verification, and unknown runtime state as blockers; a dedicated operator-facing interpretation remains open.
- [ ] Include the expected evidence bundle for CME readiness.
- [ ] Include rollback or revert guidance for bad readiness-gate changes.
- [x] Keep docs aligned with implemented backend contracts and UI behavior.
  - Evidence: Current Completed Items now reflects the implemented completed contracts and UI behavior without claiming broad production readiness.

## Full Work Order Audit Result

```yaml
final_report:
  repo_status: dirty_staged_partial_hardening
  CME_lane_status: production_blocked
  workbench_status: partially_hardened_not_validated_for_alpha_edge
  frontend_backend_sync_status: partial
  runtime_sync_status: partial
  deterministic_stage_status: partial
  probabilistic_stage_status: not_fully_documented_or_contract_enforced
  blockers_found:
    - no production-ready CME alpha/edge model evidence
    - missing canonical replay_execution_adapter artifact for CMEBacktester
    - missing or blocked CHI404/Rithmic submit-to-ack evidence
    - incomplete full-MBO Rithmic trial conversion
    - insufficient cross-artifact source-lineage validation beyond card linkage
    - tab readiness and allowed-action contract still incomplete
  blockers_fixed:
    - deterministic replay future-event buffering fail-closed tests
    - Workbench history/data gate fail-closed behavior
    - selected missing campaign artifact blocker
    - certification full-suite failure semantics
    - L3 data-quality blockers
    - CME config loading fail-closed behavior
    - lane scorecard config-loader exception blocker
    - tab navigation truthfulness
    - WFC missing-bounds blocking gate
    - CMEBacktester structural-zero evidence removed
    - CME runtime readiness contract added
    - shared tab header blocker surfacing
    - catalog manifest-only timeout caused by external cost estimation
    - canonical CME replay default events path and artifact-root lookup drift
    - runtime MODEL_CARD and VALIDATION_CARD artifact emission
    - production promotion record model-card and validation-card provenance
  blockers_remaining:
    - cross-check production promotion source lineage against campaign summary/cards/manifest
    - run canonical CME replay through the existing pipeline and attach current artifacts
    - prove alpha/edge after WFC/robustness/latency/slippage/acceptance gates
    - produce current canonical replay_execution_adapter evidence for CMEBacktester
    - complete CME acceptance runbook and operator evidence bundle
  tests_run:
    - "python -m pytest tests/backtester_validation/fast tests/test_workbench tests/test_hft3_validation tests/test_data_layer tests/test_mbo_agent_schemas.py tests/test_gate_schema.py -q --tb=short --ignore=tests/test_workbench/test_catalog_event_e2e.py"
  tests_passed: "566 passed, 1 skipped, 77 warnings in 122.16s"
  docs_updated:
    - docs/workbench/PRODUCTION_READINESS_CHECKLIST.md
  schemas_updated: []
  known_risks:
    - generated runtime validation artifacts remain unstaged
    - two out-of-scope crypto files remain untracked
    - current audit is from a dirty worktree, not a clean-main acceptance run
    - no CHI404 remote validation was run
  production_readiness_verdict: incomplete
```
