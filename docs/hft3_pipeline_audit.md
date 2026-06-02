# HFT3 Pipeline Audit — Current State (Phase 1)

> Generated from a read-only sweep of the HFT3 repository on 2026-06-02.
> Source paths are absolute and start at `C:\Users\MSI\Documents\opencode\hft3\`.

## Executive summary

HFT3 is a research-grade event-replay workbench for hypothesis-driven and
structural-model trading strategies on MBO L3 microstructure data. It already
implements most of the surfaces required by the 26-phase hardening spec —
the principal gaps are a missing `trade_manager/` package, a missing
double-WF correlator, the absence of a distinct `DefensiveModel` base class,
and a single-JSON certification registry with no atomic write or hash chain.

| # | Item | Status | One-line |
|---|------|--------|----------|
| 1 | Workbench engine dispatching HYP/PDF | EXISTS | `apps/workbench/src/run/engine.py` |
| 2 | Unified 55-model registry (44 HYP + 11 PDF) | EXISTS | `unified_registry.py` + `model_registry.yaml` |
| 3 | `WorkbenchModel` base class | EXISTS | `apps/workbench/src/core/protocol.py:40-103` |
| 4 | Distinct `DefensiveModel` base class | MISSING | defensives are role-tags, not a class |
| 5 | Combined composition (primary+def+struct) | EXISTS | `composition_orchestrator.py` + `pdf_orchestrator.py` |
| 6 | L3 MBO data loader | EXISTS | `apps/workbench/src/data/l3_loader.py` |
| 7 | Databento + Rithmic data adapters | EXISTS | `packages/data_system/...` |
| 8 | 64-dim `FeatureIndex` enum | EXISTS | `features/feature_index.py:11-126` |
| 9 | Hypothesis replay strategy on `ReplayBus` | EXISTS | `backtest_pipeline/hypothesis_replay_strategy.py` |
| 10 | Robustness pack (MC + WF + purged k-fold + param matrix) | EXISTS | `apps/workbench/src/robustness/` |
| 11 | WFC gate (Pearson / Spearman / Kendall) | EXISTS (single WF) | `apps/workbench/src/robustness/wfc/gate.py` |
| 12 | Walk-forward validator (D/C/H/R periods) | EXISTS | `decision_engine/python/src/walk_forward.py:11-78` |
| 13 | Scoring / thresholds for promotion | EXISTS | `research_pipeline/types.py:33-50` |
| 14 | Report generator + artifact tree (748 runs) | EXISTS | `report/generator.py` + `artifacts/paths.py` |
| 15 | Research pipeline (LLM + KG + candidate gen) | EXISTS | `packages/research_pipeline/` |
| 16 | Knowledge graph from research cards | EXISTS | `packages/data_layer/kg/store.py` |
| 17 | T0–T4 promotion gates | EXISTS (atomicity PARTIAL) | `hft3/validation/promotion_gate.py` |
| 18 | Immutable `CertificationRecord` | PARTIAL | no atomic write, no hash chain |
| 19 | Artifacts tree (per-run + per-campaign + per-card) | EXISTS | `artifacts/research_cards/` |
| 20 | Trade manager (signal → `OrderIntent`) | MISSING | no `trade_manager/` package |
| 21 | Execution adapter + safety guards | EXISTS (live STUB) | `packages/execution/adapters/live_broker.py:30-37` |
| 22 | Risk layer (size/loss/kill/pos/clock) | EXISTS (not wired) | `production_safety.py` + `risk_engine/` (C++) |
| 23 | NL-thesis / auto-research driver (PDF → candidate) | EXISTS (shallow) | `scripts/run_pipeline.py` |
| 24 | Streamlit UI | EXISTS | `apps/workbench/ui/` — not on autonomous path |
| 25 | CLI entry points | EXISTS (no `hft3` script) | `pyproject.toml [project.scripts]` |

## Section 1 — Backtest entrypoints

- **Workbench engine**: `apps/workbench/src/run/engine.py` — `WorkbenchEngine.run(model_id, event_id, ...)` dispatches per-model with composition and `strategy_params`.
- **Adapters**: `apps/workbench/src/adapters/{hypothesis_adapter,structural_adapter}.py`.
- **Canonical research entry**: `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --chi404-summary runtime/latency_reports/latency_summary.json` (per `docs/vault/RESEARCH_ENTRYPOINTS.md`).
- **Headless autonomous runner**: **MISSING** — no `python -m hft3.research.run_autonomous` exists.
- The current path is event-driven (CPI/macro replay), not a general end-to-end autonomous runner. Phase 2 will wrap `WorkbenchEngine` + `OptimizationRunner` + `CertificationRunner` into a single headless driver.

## Section 2 — Research / hypothesis-generation components

- `packages/research_pipeline/`: `{llm.py, document_ingestion.py, knowledge_graph.py, model_generation.py, hypothesis_parser.py, types.py, evaluation.py, deployment.py}`.
- LLM backend: Ollama `glm-5.1:cloud` (per `packages/research_pipeline/llm.py`).
- KG store: `packages/data_layer/kg/store.py`.
- NL-thesis entry: `scripts/run_pipeline.py` (autoresearch mode).
- **Gap (Phase 3)**: `document_ingestion.py` is shallow. It does not produce the 14-file output spec (`source_document_path`, `extracted_text.md`, `extracted_equations.json`, `extracted_tables.json`, `thesis_summary.json`, `assumptions.json`, `required_data.json`, `required_features.json`, `proposed_signal_logic.json`, `proposed_execution_logic.json`, `parameter_ranges.json`, `failure_modes.json`, `testable_hypotheses.json`, `experiment_translation_notes.json`).

## Section 3 — Data-loader components

- **L3 MBO loader**: `apps/workbench/src/data/l3_loader.py` with gap / duplicate / monotonic-time detection.
- **Underlying types**: `packages/features_engine/src/features/npz_feed.py` (`MBOEvent`, `OrderBook`, `FeatureIndex`).
- **Databento client**: `packages/data_system/src/{databento_client.py, npz_resolver.py, events_parser.py}`.
- **Rithmic trial lane**: `packages/data_system/rithmic_trial/` (8 sub-modules: `pipeline`, `platform`, `capture`, `connector`, `convert`, `latency`, `normalize`, `reports`, `schema`, `validate`).
- **Event catalog**: `data_system/config/events.csv` + `apps/workbench/config/model_event_binding.yaml`.
- **Gap (Phase 6)**: the loader does not tag a run with `requested_data_class` / `resolved_data_class` / `downgrade_reason`. Promotion eligibility currently does not check data-resolution match.

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
- Phase 2 will introduce a new top-level `configs/research/autonomous_hft3.yaml` schema.

## Section 6 — Alpha model interfaces

- **Base class**: `WorkbenchModel` ABC in `apps/workbench/src/core/protocol.py:40-103`.
- **Required method**: `predict(ctx) -> ActionValue`.
- **Supporting types**: `ModelConfig`, `Diagnostics`, `Phase`, `ModelRole`, `ModelComposition` in `apps/workbench/src/core/composition.py`.
- 44 hypothesis implementations + 11 PDF structural models all implement this contract.

## Section 7 — Defensive model interfaces

- **MISSING distinct base class** — defensives are a role-tag on `WorkbenchModel` (`_DEFENSIVE_IDS` in `apps/workbench/src/registry/model_catalog.py:17-29`).
- `DefensiveStub` exists in `apps/workbench/src/core/composition.py:13-50`.
- The composition orchestrator wires primary+defensive via `composition_orchestrator.py`.
- **Phase 7 will add** a real `DefensiveModel` ABC in `apps/workbench/src/core/defensive.py`, sibling to `WorkbenchModel`, with a `defend(ctx) -> FilterDecision` contract.

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
- **Gap (Phase 9)**: of the 25 robustness checks in the spec, the following are missing or partial: feature-leakage, label-leakage, timestamp-leakage, future-data leakage, parameter stability, regime stability, drawdown limit, tail-risk limit, turnover limit, transaction-cost sensitivity, slippage sensitivity, latency sensitivity, liquidity/capacity constraint, event-window stability, data-resolution eligibility, model-combination attribution, model-combination degradation detection, registry eligibility, artifact completeness.

## Section 10 — Walk-forward logic

- **Validator**: `packages/decision_engine/python/src/walk_forward.py:11-78`.
- **Periods**: Discovery, Confirmation, Holdout, Recent holdout.
- **Consumer**: `apps/workbench/src/run/campaign_runner.py`.
- **Config**: `apps/workbench/config/walk_forward.yaml`.
- **Single-WF only** — no `wf1_vs_wf2` correlator exists (Phase 10 gap).

## Section 11 — Walk-forward correlation logic

- **Single-WF WFC gate**: `apps/workbench/src/robustness/wfc/gate.py` — Pearson, Spearman, Kendall, fold-level correlation, cost-adjusted correlation, regime/universe sign check, outlier sensitivity, bootstrap CIs.
- **Config**: `apps/workbench/config/wfc_gate.yaml`.
- **Artifacts**: `apps/workbench/src/robustness/wfc/{metrics,artifacts,config}.py`.
- **Gap (Phase 10)**: no second-level correlator runs two independent WFs and asserts they agree. The spec asks for `wf1_matrix_path` vs `wf2_matrix_path` correlation; current code only correlates IS↔OOS within one split.

## Section 12 — Scoring logic

- **Gate thresholds**: `packages/research_pipeline/types.py:33-50` (`GateThresholds`, max-permissive defaults).
- **Metrics**: `sharpe`, `profit_factor`, `max_drawdown`, `cagr` in `apps/workbench/src/robustness/wfc/metrics.py`.
- **WFC gate thresholds**: `pearson_min`, `spearman_min`, `correlation_p_value_max` from `wfc_gate.yaml`.
- **Gap (Phase 8)**: the 16 gate categories in the spec are not unified under a single `gate_result.py` schema.

## Section 13 — Candidate / champion / model registry

- **Certification registry**: `packages/hft3/validation/certification_registry.py` — `CertificationRecord` written to a single JSON file at `runtime/validation/certification_registry.json`.
- **Promotion gate**: `packages/hft3/validation/promotion_gate.py` — `PromotionGateResult`, T0–T4 levels.
- **Stamp metadata**: `packages/hft3/validation/research_stamp.py`.
- **Fast gate report**: `packages/hft3/validation/fast_gate_report.py`.
- **Staleness check**: `packages/hft3/validation/certification_staleness.py`.
- **Gap (Phase 11)**: the registry is a single JSON file with no `os.replace` atomic write, no file lock, and no SHA-256 hash chain. Risk of torn writes on concurrent promotion.

## Section 14 — Reporting logic

- **Report generator**: `apps/workbench/src/report/generator.py` (markdown + research card).
- **JSON schema**: `apps/workbench/schemas/run_report.schema.json`.
- **Artifact paths**: `apps/workbench/src/artifacts/paths.py`.
- **Per-run manifest**: `apps/workbench/src/run/engine.py:118` writes `manifest.json` in artifact dir.
- **Gap (Phase 13)**: the 22 required `report.md` sections are not all present; the current generator does not include walk-forward correlation results or model-combination attribution.

## Section 15 — Artifact directories

- `artifacts/research_cards/{workbench_runs (748 dirs), parity, kg, migration, single_run_*, *_replay, workbench_browser_smoke}`.
- `runtime/{latency_reports, replay_audits, validation, audits, event_snapshots, reports, data_audits, schemas, chi404}`.
- `research_cards/{crypto, pipeline_runs, kg}`.
- Helpers: `apps/workbench/src/artifacts/paths.py`.
- **Gap (Phase 12)**: the 17-file artifact bundle spec is not enforced.

## Section 16 — Trade-management components

- **MISSING** — no `trade_manager/` package.
- The only existing "trade manager" is the C++ `risk_engine/include/risk_manager.hpp`, which is a **risk** monitor, not a signal→intent orchestrator.
- `OrderIntent` exists in `packages/execution/interfaces.py` (52 matches) but no orchestrator turns a registered model + market event into a sized, risk-checked, time-stamped intent.
- **Phases 14–23** will create `packages/trade_manager/` from scratch.

## Section 17 — Risk-layer components

- **Python**: `packages/execution/production_safety.py` — `StaleDataMonitor`, `DisconnectMonitor`, `ClockDriftMonitor`, `PositionMismatchMonitor`, `DailyLossLimitFlattenMonitor`.
- **C++**: `risk_engine/{include/risk_manager.hpp, src/risk_manager.cpp}` — `FailureState` enum.
- **Live-mode env vars**: `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`, `LIVE_KILL_SWITCH`, `LIVE_RISK_ENABLED`.
- **Gap (Phase 17)**: neither `WorkbenchEngine` nor the live broker (stub) calls into `production_safety.py`. The C++ `RiskManager` is not exposed via pybind. There is no `validate_live_env()` function.

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
- **Gap (Phase 2)**: add `hft3` console script + `python -m hft3.research.run_autonomous` entry.

## Section 21 — Manual gates / human-dependent steps

- **None found in the code path** (grep over `.py` for `input(`, `click.prompt`, `approval` interactive prompts: 0 matches in code).
- The 2 `approval` matches are field names (`approval_status`), not prompts.
- All gating is config-driven (`personal_lock.yaml`) or gate-threshold-driven (WFC / T0–T4).

## Section 22 — Current failure points

1. **No top-level autonomous runner** — every component must be invoked manually.
2. **No atomic registry write** — risk of torn writes on T4 promotion.
3. **No double-WF correlator** — single WF means the system can pass on one window and fail on another.
4. **Defensives are role-tags, not a class** — implicit composition contract.
5. **Live broker adapter is a stub** — no real live execution path.
6. **C++ `RiskManager` not wired into Python** — risk is enforced only at the C++ engine boundary, not the backtest.
7. **No `hft3` console script** — every CLI invocation is module-form.
8. **Production safety monitors not wired into adapter path** — `StaleDataMonitor` etc. are implemented but unused outside unit tests.

## Section 23 — Gaps between stages

| Stage | Gap |
|-------|-----|
| Hypothesis → experiment spec | Partial: `document_ingestion.py` is shallow, no 14-file output |
| Backtest → robustness | OK: `WorkbenchEngine` invokes robustness pack |
| Scoring → registry | Partial: scoring exists, promotion gate exists, but atomicity missing |
| Registry → trade manager | **MISSING**: no trade manager at all |
| Trade manager → execution | N/A: no trade manager |
| Trade manager → observer | N/A: no trade manager |
| Trade manager → session report | N/A: no trade manager |

## Section 24 — Files that require modification (M1)

Phase 1 (audit): `docs/hft3_pipeline_audit.md` (this file).  
Phase 2 (autonomous runner): new `packages/hft3/research/run_autonomous.py`, new `configs/research/autonomous_hft3.yaml`, new `pyproject.toml [project.scripts]` entry.  
Phase 3 (intake): `packages/research_pipeline/document_ingestion.py` — extend with 14-file output writer.  
Phase 4 (LLM boundary): new test `tests/test_llm_cannot_promote_model.py`.  
Phase 5 (backtest hardening): `apps/workbench/src/run/engine.py` — add 33-timestamp capture to `run()`.  
Phase 6 (L3 labeling): `apps/workbench/src/data/l3_loader.py` — add resolution tags.  
Phase 7 (defensive ABC): new `apps/workbench/src/core/defensive.py`, refactor `_DEFENSIVE_IDS` to use it.  
Phase 8 (gate schema): new `apps/workbench/src/validation/gate_result.py`, refactor existing gates.  
Phase 9 (robustness): `apps/workbench/src/robustness/pack.py` — add missing checks.  
Phase 10 (double WF): new `apps/workbench/src/robustness/wfc/double_wf.py`.  
Phase 11 (atomicity): `packages/hft3/validation/certification_registry.py` — hash-chain + `os.replace`.  

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

1. **`packages/trade_manager/`** — create `TradeManager` class for signal→OrderIntent orchestration.
2. **Atomic certification registry** — `os.replace` + SHA-256 hash chain + file lock.
3. **Double-WF correlator** — `wfc/double_wf.py` for WF1↔WF2 agreement.
4. **`DefensiveModel` ABC** — sibling to `WorkbenchModel`, lock the defensive contract.
5. **Wire `production_safety.py` into the adapter path** — enforce risk in `TradeManager.submit_order`.
6. **Headless autonomous runner** — `python -m hft3.research.run_autonomous` + `hft3` console script.
7. **L3 data-resolution labeling** — `requested_data_class` / `resolved_data_class` / `downgrade_reason` per run.
8. **Unified gate schema** — `gate_result.py` covering the 16 spec categories.
9. **`test_llm_cannot_promote_model`** — boundary test for the LLM research layer.
10. **Hot-path / do-not-touch CI guard** — fail the build if `rithmic_gateway/`, `vendor/`, `*/cpp/`, `risk_engine/` appear in a PR diff.

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
