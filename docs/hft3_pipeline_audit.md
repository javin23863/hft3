# HFT3 Pipeline Audit — Current State (Phase 1)

> Generated from a read-only sweep of the HFT3 repository on 2026-06-02.
> Source paths are absolute and start at `C:\Users\MSI\Documents\opencode\hft3\`.

## Executive summary

HFT3 is a research-grade event-replay workbench for hypothesis-driven and
structural-model trading strategies on MBO L3 microstructure data. It already
implements most of the surfaces required by the 26-phase hardening spec. The
principal gaps are incomplete Trade Manager order/risk/execution modules,
autonomous runner wiring to real Workbench backtest/robustness evidence,
double-WF matrix wiring into campaign/autonomous promotion, and live
observer/execution integration.

| # | Item | Status | One-line |
|---|------|--------|----------|
| 1 | Workbench engine dispatching HYP/PDF | EXISTS | `apps/workbench/src/run/engine.py` |
| 2 | Unified 55-model registry (44 HYP + 11 PDF) | EXISTS | `unified_registry.py` + `model_registry.yaml` |
| 3 | `WorkbenchModel` base class | EXISTS | `apps/workbench/src/core/protocol.py:40-103` |
| 4 | Distinct `DefensiveModel` base class | EXISTS | `apps/workbench/src/core/defensive.py` |
| 5 | Combined composition (primary+def+struct) | EXISTS | `composition_orchestrator.py` + `pdf_orchestrator.py` |
| 6 | L3 MBO data loader | EXISTS | `apps/workbench/src/data/l3_loader.py` |
| 7 | Databento + Rithmic data adapters | EXISTS | `packages/data_system/...` |
| 8 | 64-dim `FeatureIndex` enum | EXISTS | `features/feature_index.py:11-126` |
| 9 | Hypothesis replay strategy on `ReplayBus` | EXISTS | `backtest_pipeline/hypothesis_replay_strategy.py` |
| 10 | Robustness pack (MC + WF + purged k-fold + param matrix) | EXISTS | `apps/workbench/src/robustness/` |
| 11 | WFC gate (Pearson / Spearman / Kendall) | EXISTS | `apps/workbench/src/robustness/wfc/gate.py` + `double_wf.py` |
| 12 | Walk-forward validator (D/C/H/R periods) | EXISTS | `decision_engine/python/src/walk_forward.py:11-78` |
| 13 | Scoring / thresholds for promotion | EXISTS | `research_pipeline/types.py:33-50` |
| 14 | Report generator + artifact tree (748 runs) | EXISTS | `report/generator.py` + `artifacts/paths.py` |
| 15 | Research pipeline (LLM + KG + candidate gen) | EXISTS | `packages/research_pipeline/` |
| 16 | Knowledge graph from research cards | EXISTS | `packages/data_layer/kg/store.py` |
| 17 | T0–T4 promotion gates | EXISTS | `hft3/validation/promotion_gate.py` |
| 18 | Immutable `CertificationRecord` | EXISTS | atomic write, file lock, SHA-256 hash chain |
| 19 | Artifacts tree (per-run + per-campaign + per-card) | EXISTS | `artifacts/research_cards/` |
| 20 | Trade manager (signal → risk decision) | PARTIAL | Phase 14/15/16/17 handoff, signal ingress, inert order intent, and inert risk decisions exist; no execution orchestration yet |
| 21 | Execution adapter + safety guards | EXISTS (live STUB) | `packages/execution/adapters/live_broker.py:30-37` |
| 22 | Risk layer (size/loss/kill/pos/clock) | EXISTS (Trade Manager decision layer) | `trade_manager/risk_layer.py`, `production_safety.py`, and `risk_engine/` (C++) |
| 23 | NL-thesis / auto-research driver (PDF → candidate) | EXISTS | 14-file intake bundle + `scripts/run_pipeline.py` |
| 24 | Streamlit UI | EXISTS | `apps/workbench/ui/` — not on autonomous path |
| 25 | CLI entry points | EXISTS (no `hft3` script) | `pyproject.toml [project.scripts]` |

## Section 1 — Backtest entrypoints

- **Workbench engine**: `apps/workbench/src/run/engine.py` — `WorkbenchEngine.run(model_id, event_id, ...)` dispatches per-model with composition and `strategy_params`.
- **Adapters**: `apps/workbench/src/adapters/{hypothesis_adapter,structural_adapter}.py`.
- **Canonical research entry**: `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --chi404-summary runtime/latency_reports/latency_summary.json` (per `docs/vault/RESEARCH_ENTRYPOINTS.md`).
- **Headless autonomous runner**: `python -m hft3.research.run_autonomous` exists and writes auditable scaffold artifacts.
- The runner is not yet wired to `WorkbenchEngine` observed backtest, robustness, and double-WF matrix evidence, so it quarantines rather than promotes.

## Section 2 — Research / hypothesis-generation components

- `packages/research_pipeline/`: `{llm.py, document_ingestion.py, knowledge_graph.py, model_generation.py, hypothesis_parser.py, types.py, evaluation.py, deployment.py}`.
- LLM backend: Ollama `glm-5.1:cloud` (per `packages/research_pipeline/llm.py`).
- KG store: `packages/data_layer/kg/store.py`.
- NL-thesis entry: `scripts/run_pipeline.py` (autoresearch mode).
- **Phase 3 done**: `intake_schema.py` / `intake_bundle.py` produce the 14-file output spec (`source_document_path`, `extracted_text.md`, `extracted_equations.json`, `extracted_tables.json`, `thesis_summary.json`, `assumptions.json`, `required_data.json`, `required_features.json`, `proposed_signal_logic.json`, `proposed_execution_logic.json`, `parameter_ranges.json`, `failure_modes.json`, `testable_hypotheses.json`, `experiment_translation_notes.json`).

## Section 3 — Data-loader components

- **L3 MBO loader**: `apps/workbench/src/data/l3_loader.py` with gap / duplicate / monotonic-time detection.
- **Underlying types**: `packages/features_engine/src/features/npz_feed.py` (`MBOEvent`, `OrderBook`, `FeatureIndex`).
- **Databento client**: `packages/data_system/src/{databento_client.py, npz_resolver.py, events_parser.py}`.
- **Rithmic trial lane**: `packages/data_system/rithmic_trial/` (8 sub-modules: `pipeline`, `platform`, `capture`, `connector`, `convert`, `latency`, `normalize`, `reports`, `schema`, `validate`).
- **Event catalog**: `data_system/config/events.csv` + `apps/workbench/config/model_event_binding.yaml`.
- **Phase 6 done**: `packages/hft3/data_class.py` defines data-resolution tags, downgrade reasons, and promotion eligibility gate conversion.

## Section 4 — Feature-generation components

- **Feature engine**: `packages/features_engine/src/`.
- **64-dim `FeatureIndex`**: `packages/features_engine/src/features/feature_index.py:11-126`.
- **Structural / PDF models (11)**: `packages/features_engine/src/structural_models/model_01..11.py`.
- **Hypotheses (44)**: `packages/features_engine/src/hypotheses/`.
- **HYP registry**: `get_active_hypotheses()` (44). **PDF registry**: `get_structural_models()` (7 visible in the source; the 11 figure may include per-pdf helpers; clarify in Phase 7). **Total registry**: 55 in the unified registry.

## Section 5 — Experiment / campaign configuration files

- Workbench configs: `apps/workbench/config/{model_catalog.yaml, wfc_gate.yaml, walk_forward.yaml, model_event_binding.yaml, ...}`.
- Hypothesis/PDF registry: `packages/features_engine/config/model_registry.yaml`.
- Trial config: `data_system/config/rithmic_trial.yaml`.
- Phase 2 introduced `configs/research/autonomous_hft3.yaml`; Workbench integration remains pending.

## Section 6 — Alpha model interfaces

- **Base class**: `WorkbenchModel` ABC in `apps/workbench/src/core/protocol.py:40-103`.
- **Required method**: `predict(ctx) -> ActionValue`.
- **Supporting types**: `ModelConfig`, `Diagnostics`, `Phase`, `ModelRole`, `ModelComposition` in `apps/workbench/src/core/composition.py`.
- 44 hypothesis implementations + 11 PDF structural models all implement this contract.

## Section 7 — Defensive model interfaces

- **Distinct base class**: `DefensiveModel` in `apps/workbench/src/core/defensive.py` is a sibling contract to `WorkbenchModel`.
- `DefensiveStub` exists in `apps/workbench/src/core/composition.py:13-50`.
- The composition orchestrator wires primary+defensive via `composition_orchestrator.py`.
- **Phase 7 done**: `DefensiveModel.defend(ctx) -> FilterDecision` locks the defensive contract and `MODEL_COMBINATIONS` enumerates the required test matrix.

## Section 8 — Hybrid model logic

- **Composition orchestrator** (primary+defensive): `apps/workbench/src/registry/composition_orchestrator.py`.
- **PDF orchestrator** (topo-sorted structural execution): `apps/workbench/src/registry/pdf_orchestrator.py`.
- **CLI flag**: `--composition` in `apps/workbench/src/run/composition_cli.py`.
- **Artifact**: `composition.json` written by `apps/workbench/src/run/job_manager.py:43-46` when defensive stubs are present.

## Section 9 — Robustness tests

- **Aggregator**: `apps/workbench/src/robustness/pack.py`.
- **Purged k-fold**: `apps/workbench/src/robustness/purged_cv.py`.
- **Parameter matrix**: `apps/workbench/src/optimization/matrix_runner.py` + `param_matrix.py`.
- **Monte Carlo**: present in the pack.
- **Phase 9 done**: `apps/workbench/src/robustness/pack.py::REQUIRED_ROBUSTNESS_CHECKS` registers all 25 required checks. Checks with unavailable inputs are `PENDING`/blocking rather than silently passing.

## Section 10 — Walk-forward logic

- **Validator**: `packages/decision_engine/python/src/walk_forward.py:11-78`.
- **Periods**: Discovery, Confirmation, Holdout, Recent holdout.
- **Consumer**: `apps/workbench/src/run/campaign_runner.py`.
- **Config**: `apps/workbench/config/walk_forward.yaml`.
- **Single-WF campaign path** — the Phase 10 double-WF correlator exists, but campaign/autonomous wiring still emits pending/stub artifacts until independent WF matrices are available.

## Section 11 — Walk-forward correlation logic

- **Single-WF WFC gate**: `apps/workbench/src/robustness/wfc/gate.py` — Pearson, Spearman, Kendall, fold-level correlation, cost-adjusted correlation, regime/universe sign check, outlier sensitivity, bootstrap CIs.
- **Config**: `apps/workbench/config/wfc_gate.yaml`.
- **Artifacts**: `apps/workbench/src/robustness/wfc/{metrics,artifacts,config}.py`.
- **Phase 10 done / wiring gap remains**: `apps/workbench/src/robustness/wfc/double_wf.py` implements `wf1_matrix_path` vs `wf2_matrix_path` correlation. Campaign/autonomous paths still need real independent WF matrix inputs before promotion can depend on it.

## Section 12 — Scoring logic

- **Gate thresholds**: `packages/research_pipeline/types.py:33-50` (`GateThresholds`, max-permissive defaults).
- **Metrics**: `sharpe`, `profit_factor`, `max_drawdown`, `cagr` in `apps/workbench/src/robustness/wfc/metrics.py`.
- **WFC gate thresholds**: `pearson_min`, `spearman_min`, `correlation_p_value_max` from `wfc_gate.yaml`.
- **Phase 8 done**: `packages/hft3/validation/gate_result.py` defines the unified 17-category `GateResult` schema and atomic `robustness_gates.json` writer.

## Section 13 — Candidate / champion / model registry

- **Certification registry**: `packages/hft3/validation/certification_registry.py` — `CertificationRecord` plus JSONL audit log with atomic writes, file lock, and SHA-256 hash chain.
- **Promotion gate**: `packages/hft3/validation/promotion_gate.py` — `PromotionGateResult`, T0–T4 levels.
- **Stamp metadata**: `packages/hft3/validation/research_stamp.py`.
- **Fast gate report**: `packages/hft3/validation/fast_gate_report.py`.
- **Staleness check**: `packages/hft3/validation/certification_staleness.py`.
- **Phase 11 done**: registry updates use `os.replace`, fsync, cross-platform lock, and hash-chain audit verification.

## Section 14 — Reporting logic

- **Report generator**: `apps/workbench/src/report/generator.py` (markdown + research card).
- **JSON schema**: `apps/workbench/schemas/run_report.schema.json`.
- **Artifact paths**: `apps/workbench/src/artifacts/paths.py`.
- **Per-run manifest**: `apps/workbench/src/run/engine.py:118` writes `manifest.json` in artifact dir.
- **Phase 13 done**: the autonomous report generator covers the 22 required sections; observed WFC/model-combination values remain pending until Workbench integration feeds them.

## Section 15 — Artifact directories

- `artifacts/research_cards/{workbench_runs (748 dirs), parity, kg, migration, single_run_*, *_replay, workbench_browser_smoke}`.
- `runtime/{latency_reports, replay_audits, validation, audits, event_snapshots, reports, data_audits, schemas, chi404}`.
- `research_cards/{crypto, pipeline_runs, kg}`.
- Helpers: `apps/workbench/src/artifacts/paths.py`.
- **Phase 12 done**: artifact bundle manifest/schema validation enforces the required run artifact set; runner values remain scaffolded where Workbench evidence is pending.

## Section 16 — Trade-management components

- **Phase 14 handoff exists** — `packages/trade_manager/manager.py` validates latest `PROMOTED` records, required registry fields, and run manifest evidence.
- **Phase 15 signal ingress exists** — `packages/trade_manager/signals.py` defines `ModelSignal` and validates side-effect-free active-model signal envelopes.
- **Phase 16 order intent exists** — `packages/trade_manager/order_intent.py` defines an inert 18-field `TradeManagerOrderIntent` distinct from adapter-level execution intents.
- **Phase 17 risk-decision layer exists** — `packages/trade_manager/risk_layer.py` evaluates exact stored intents with configured static checks and `production_safety.py` monitor results.
- The only existing "trade manager" is the C++ `risk_engine/include/risk_manager.hpp`, which is a **risk** monitor, not a signal→intent orchestrator.
- `OrderIntent` exists in `packages/execution/interfaces.py` (52 matches); Phase 17 creates one only as production-safety monitor input, not as a routed execution request.
- **Phases 18–23** still need execution, state, monitoring, kill-switch, observer, and session modules.

## Section 17 — Risk-layer components

- **Trade Manager**: `packages/trade_manager/risk_layer.py` — static configured risk checks, adapter-context production-safety invocation, and inert `TradeManagerRiskDecision` output.
- **Python production safety**: `packages/execution/production_safety.py` — `StaleDataMonitor`, `DisconnectMonitor`, `ClockDriftMonitor`, `PositionMismatchGuard`, `DailyLossLimitFlatten`.
- **C++**: `risk_engine/{include/risk_manager.hpp, src/risk_manager.cpp}` — `FailureState` enum.
- **Live-mode env vars**: `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`, `LIVE_KILL_SWITCH`, `LIVE_RISK_ENABLED`.
- **Gap**: Phase 17 stores risk decisions only. Neither an order state machine nor the live broker (stub) consumes the decision yet. The C++ `RiskManager` is not exposed via pybind. There is no `validate_live_env()` function.

## Section 18 — Execution-adapter components

- **Interface**: `packages/execution/interfaces.py` — `OrderIntent`, `OrderEvent`, `OrderEventType`, `AccountState`, `ExecutionAdapter` Protocol.
- **Factory**: `packages/execution/adapter_factory.py`.
- **Safety helpers**: `packages/execution/safety.py`.
- **Adapters**:
  - `hftbacktest_simulated_exchange.py` — simulated
  - `paper_broker.py` — paper
  - `live_broker.py:30-37` — **STUB** (returns `ORDER_REJECTED` with reason `"live_adapter_stub_not_wired"`)
- **Gap (Phase 19)**: there is no Rithmic live adapter implementation; only protocol stubs. The strategy/model layer should not import broker APIs directly — this is currently true but unenforced (no test).

## Section 19 — Streamlit / UI dependencies

- `apps/workbench/ui/{app.py, analyst_panel.py, campaign_panel.py, workflow_tabs.py, flow_state.py}`.
- `ui/app.py:161-166` exposes Pearson, Spearman, per-fold correlations.
- `ui/workflow_tabs.py` is the multi-tab workflow.
- **Not** on the autonomous path. **Do not modify** in M1.

## Section 20 — CLI / headless paths

- `pyproject.toml [project.scripts]`: defines `workbench` and `economic-event-universe`.
- **No `hft3` console script**.
- Launchers: `run_workbench.py`, `python -m workbench`, `python -m data_system.rithmic_trial.pipeline`.
- Headless research entry: `python scripts/run_event_replay.py --event-id ...`.
- **Remaining CLI gap**: `python -m hft3.research.run_autonomous` exists; add a top-level `hft3` console script if the team wants one-command launch.

## Section 21 — Manual gates / human-dependent steps

- **None found in the code path** (grep over `.py` for `input(`, `click.prompt`, `approval` interactive prompts: 0 matches in code).
- The 2 `approval` matches are field names (`approval_status`), not prompts.
- All gating is config-driven (`personal_lock.yaml`) or gate-threshold-driven (WFC / T0–T4).

## Section 22 — Current failure points

1. **Autonomous runner is still scaffolded** — it writes honest blocking gates but does not yet invoke `WorkbenchEngine`.
2. **Workbench backtest-to-robustness evidence is not wired into the autonomous runner** — Workbench emits Phase 5/9 artifacts; the runner still writes stub backtest metrics.
3. **Double-WF correlator exists but is not campaign/autonomous promotion input** — real independent WF matrix wiring is still pending.
4. **Trade Manager is partial** — Phase 14 registry handoff, Phase 15 signal ingress, Phase 16 order-intent schema, and Phase 17 risk decisions exist, but no execution orchestration exists yet.
5. **Live broker adapter is a stub** — no real live execution path.
6. **C++ `RiskManager` not wired into Python** — risk is enforced only at the C++ engine boundary, not the backtest.
7. **Production safety monitors are only used for Trade Manager decisions** — no adapter path consumes risk approvals/rejections yet.
8. **No production observer/session layer** — observer view, kill switch, position reconciliation, and session artifacts are still absent.

## Section 23 — Gaps between stages

| Stage | Gap |
|-------|-----|
| Hypothesis → experiment spec | OK for Phase 3 intake bundles; autonomous runner experiment specs remain scaffolded until Workbench integration |
| Backtest → robustness | OK inside Workbench; pending in autonomous runner |
| Scoring → registry | Atomic registry writes exist; autonomous runner still quarantines because observed metrics are pending |
| Registry → trade manager | OK for Phase 14/15/16/17 handoff, signal ingress, inert order intent, and inert risk decision; activation validates latest `PROMOTED` record and manifest evidence, accepts validated `ModelSignal` envelopes, creates non-routed `TradeManagerOrderIntent` envelopes, then records risk decisions |
| Trade manager → execution | **MISSING**: no execution orchestration yet |
| Trade manager → observer | **MISSING**: no observer path yet |
| Trade manager → session report | **MISSING**: no session report path yet |

## Section 24 — Phase implementation file map (M1)

Phase 1 (audit): `docs/hft3_pipeline_audit.md` (this file).
Phase 2 (autonomous runner): `packages/hft3/research/run_autonomous.py`, `configs/research/autonomous_hft3.yaml`; top-level `hft3` console script remains absent.
Phase 3 (intake): `packages/research_pipeline/{intake_schema,intake_bundle,extractors}.py` — 14-file output writer and extractors.
Phase 4 (LLM boundary): `tests/test_research_intake.py` boundary checks.
Phase 5 (backtest hardening): `apps/workbench/src/run/engine.py`, `apps/workbench/src/core/trade_audit.py` — 33-timestamp capture.
Phase 6 (L3 labeling): `packages/hft3/data_class.py` — data-resolution tags and gate conversion.
Phase 7 (defensive ABC): `apps/workbench/src/core/defensive.py` — `DefensiveModel` and combination matrix.
Phase 8 (gate schema): `packages/hft3/validation/gate_result.py` — 17-category gate schema and atomic writer.
Phase 9 (robustness): `apps/workbench/src/robustness/pack.py` — 25-check schema and honest pending/fail statuses.
Phase 10 (double WF): `apps/workbench/src/robustness/wfc/double_wf.py` — independent WF matrix correlation.
Phase 11 (atomicity): `packages/hft3/validation/certification_registry.py` — hash-chain, file lock, and `os.replace`.
Phase 14 (Trade Manager handoff): `packages/trade_manager/manager.py` — latest promoted registry record to active-model manifest handoff.
Phase 15 (Trade Manager signal ingress): `packages/trade_manager/signals.py` — active-model signal envelope before order-intent conversion.
Phase 16 (Trade Manager order intent): `packages/trade_manager/order_intent.py` — inert 18-field order-intent schema before risk/execution.
Phase 17 (Trade Manager risk layer): `packages/trade_manager/risk_layer.py`, `configs/risk/limits.yaml` — inert risk decisions before order state/execution.

## Section 25 — Files that should remain untouched (hot path)

- `rithmic_gateway/RApiPlus/` — proprietary Rithmic SDK (extracted zip, gitignored).
- `rithmic_gateway/RApiPlus_new/` — same.
- `rithmic_gateway/{include, src, tools}` — our C++ adapter around the SDK (do not modify for M1; only fix bugs).
- `vendor/` — vendored code.
- `packages/decision_engine/cpp/{include, src}` — C++ decision runtime.
- `packages/features_engine/cpp/{include, src}` — C++ features runtime.
- `risk_engine/{include, src}` — C++ `RiskManager`.
- `apps/workbench/ui/` — Streamlit; not on autonomous path.

## Top-10 hardening priorities (consolidated)

1. **Phase 17 Trade Manager risk layer** — add risk-layer enforcement after the Phase 16 inert order-intent schema.
2. **Autonomous Workbench integration** — feed real `WorkbenchEngine` backtest, Phase 5 audit, Phase 9 robustness, and scoring evidence into the runner.
3. **Campaign/autonomous double-WF wiring** — feed independent WF matrices into `double_wf.py` and promotion gates.
4. **Wire `production_safety.py` into the adapter path** — enforce risk in `TradeManager.submit_order`.
5. **Live broker adapter implementation** — replace the live stub with a CHI404-only execution path.
6. **Production observer/session layer** — observer view, kill switch, position reconciliation, and session artifacts.
7. **C++ `RiskManager` Python/backtest integration** — expose parity checks outside the C++ engine boundary.
8. **Top-level `hft3` console script** — optional one-command wrapper for the existing module runner.
9. **Hot-path / do-not-touch CI guard** — fail the build if `rithmic_gateway/`, `vendor/`, `*/cpp/`, `risk_engine/` appear in a PR diff.
10. **Runner metric de-scaffolding** — replace remaining stub scoring/report values with observed Workbench artifacts before promotion can pass.

## Quick file map

- **Workbench engine / run / campaign**: `apps/workbench/src/run/`, `apps/workbench/src/optimization/`
- **Workbench core (protocol / composition)**: `apps/workbench/src/core/`
- **Workbench registry**: `apps/workbench/src/registry/`
- **Workbench robustness + WFC**: `apps/workbench/src/robustness/`
- **Workbench data plane**: `apps/workbench/src/data/`
- **Workbench UI (do not modify)**: `apps/workbench/ui/`
- **Workbench configs**: `apps/workbench/config/`
- **55-model registry YAML**: `packages/features_engine/config/model_registry.yaml`
- **Hypotheses (44)**: `packages/features_engine/src/hypotheses/`
- **Structural / PDF (11)**: `packages/features_engine/src/structural_models/`
- **64-dim FeatureIndex**: `packages/features_engine/src/features/feature_index.py`
- **Walk-forward validator**: `packages/decision_engine/python/src/walk_forward.py`
- **Backtest engine + replay matrix + WFC integration**: `packages/backtest_pipeline/src/`
- **Replay bus**: `packages/replay/`
- **Research pipeline (LLM / KG)**: `packages/research_pipeline/`, `packages/data_layer/`
- **Data system (Databento + Rithmic trial)**: `packages/data_system/`
- **Execution adapters + safety**: `packages/execution/`
- **Certification / promotion gates (T0–T4)**: `packages/hft3/validation/`
- **C++ RiskManager (do not touch)**: `risk_engine/`
- **Rithmic C++ SDK (do not touch)**: `rithmic_gateway/`
- **C++ decision engine + features (do not touch)**: `packages/decision_engine/cpp/`, `packages/features_engine/cpp/`
- **Vendored code (do not touch)**: `vendor/`
- **Artifacts tree**: `artifacts/research_cards/workbench_runs/` (748 runs)
- **Runtime audits / reports**: `runtime/`
- **CHI404 host scripts**: `infrastructure/chi404/`
- **AGENTS / BLUEPRINT**: `AGENTS.md`, `BLUEPRINT.md`
