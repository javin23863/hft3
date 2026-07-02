# Research system execution order

**Start here** after [GETTING_STARTED.md](GETTING_STARTED.md) and [../REPO_STATE.md](../REPO_STATE.md).

This is the single chronological map for feature-family research. Do not use competing “start here” documents. For agent delegation and graph gates, continue to [../ai/ONBOARDING.md](../ai/ONBOARDING.md).

**Workstream prompt:** [../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md](../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md)  
**Implementation audit:** [../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md)  
**CLI commands (canonical):** [../vault/RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md)

---

## How to read this document

Each step lists: **purpose**, **canonical document**, **primary code**, **configuration**, **tests**, **inputs**, **outputs**, **next step**.

```text
1. Invariants
2. Data sources and clocks
3. Feature sources
4. Feature-family definitions
5. Synchronization and PIT
6. Feature recipes and candidates
7. Model registry
8. HftBacktest data validation
9. HftBacktest strategy run
10. Post-HBT evaluation
11. Autonomous learning
12. Artifacts and cockpit
13. Local unit tests
14. Smoke tests
15. Paid-compute readiness
16. Full campaign execution
```

---

## 1. System and research invariants

| Field | Value |
|-------|-------|
| **Purpose** | Filtration F_t, no lookahead, walk-forward, lane topology before any run |
| **Document** | [../../BLUEPRINT.md](../../BLUEPRINT.md), [../REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md), [../VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md) |
| **Code** | `packages/backtest_pipeline/src/ontology_gate.py`, `apps/workbench/src/robustness/wfc/` |
| **Config** | `apps/workbench/config/walk_forward.yaml`, `wfc_gate.yaml` |
| **Tests** | `tests/backtest_pipeline/test_ontology_gate.py`, `tests/backtester_validation/fast/` |
| **Inputs** | Vault + authority PDFs under `docs/references/` |
| **Outputs** | Ontology gate receipts under `runtime/reports/ontology_gate_*.json` |
| **Next** | Step 2 — data clocks |

---

## 2. Data sources and clocks

| Field | Value |
|-------|-------|
| **Purpose** | Know which NPZ, sensors, and calendars are trusted; separate discovery vs holdout clocks |
| **Document** | [../research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md](../research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md), [../vault/ECONOMIC_EVENT_UNIVERSE.md](../vault/ECONOMIC_EVENT_UNIVERSE.md) |
| **Code** | `packages/data_system/`, `apps/workbench/src/data/event_catalog.py`, `packages/replay/replay_session.py` |
| **Config** | `packages/data_system/config/events.csv`, `data/npz/` (Databento trusted), Rithmic trial quarantine |
| **Tests** | `tests/test_workbench/test_event_catalog.py`, `tests/test_economic_event_universe/` |
| **Inputs** | Raw Databento NPZ, optional VIX sensor NPZ, CHI404 latency exports |
| **Outputs** | Catalog coverage summaries, dataset manifests on replay runs |
| **Next** | Step 3 — atomic features |

---

## 3. Feature sources (atomic extraction)

| Field | Value |
|-------|-------|
| **Purpose** | 64-dim fs_v1 slots, MBO-derived scalars, C++ golden parity |
| **Document** | [../../specs/FEATURES.md](../../specs/FEATURES.md) |
| **Code** | `packages/features_engine/src/features/mbo_features.py`, `feature_index.py`, C++ extractor |
| **Config** | `HFT3_FEATURE_BACKEND`, `FEATURE_VERSION=fs_v1` |
| **Tests** | `tests/test_cpp_feature_golden.py`, `tests/test_feature_parity.py` |
| **Inputs** | Normalized MBO NPZ |
| **Outputs** | Per-tick feature vectors, slot metadata |
| **Next** | Step 4 — families |

---

## 4. Feature-family definitions

| Field | Value |
|-------|-------|
| **Purpose** | Eight canonical families; catalog eligibility vs model consumption |
| **Document** | [../project/VECTORBT_SCREENING_ENGINE_SPEC.md](../project/VECTORBT_SCREENING_ENGINE_SPEC.md) § Feature-Complete Data Plane |
| **Code** | `packages/backtest_pipeline/src/feature_plane.py` |
| **Config** | [../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml](../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml) |
| **Tests** | `tests/backtest_pipeline/test_feature_plane.py` |
| **Inputs** | Family definitions, consumption vocabulary |
| **Outputs** | `feature_usage_manifest` template per screen |
| **Next** | Step 5 — sync |

**Families:** `primary_fs_v1`, `cross_asset_futures`, `vix_vvix_sensor`, `vix_options`, `cme_options_context`, `macro_context`, `continuous_session`, `latency_state`.

---

## 5. Feature synchronization and PIT rules

| Field | Value |
|-------|-------|
| **Purpose** | One causal market-state snapshot per decision timestamp |
| **Document** | [../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md) (data-clock diagram) |
| **Code** | `packages/features_engine/src/pipeline/market_state_pipeline.py`, `packages/replay/market_data_adapter.py`, `packages/replay/sensor_feature_adapter.py` |
| **Config** | `latency_ms`, `vix_extra_latency_ms`, replay multi-adapter sync |
| **Tests** | `tests/test_cross_asset_replay.py`, `tests/test_replay_feature_latency.py` |
| **Inputs** | Per-symbol streams, sensor NPZ, target decision ts |
| **Outputs** | Time-aligned family blocks with provenance (not flat dict) |
| **Next** | Step 6 — recipes |

**Rule:** `latest_admissible_source_ts = target_decision_ts − feature_transport_latency`.

---

## 6. Feature recipes and candidate definitions

| Field | Value |
|-------|-------|
| **Purpose** | Declare which families, symbols, lags, and gates each candidate uses; separate from execution params |
| **Document** | [../project/VBT_MODEL_ONTOLOGY.md](../project/VBT_MODEL_ONTOLOGY.md), [../project/VBT_HYPOTHESIS_FEATURE_MAP.md](../project/VBT_HYPOTHESIS_FEATURE_MAP.md) |
| **Code** | `packages/research_pipeline/model_generation.py`, `idea_generation.py`, `types.py` (extend for recipes — Phase 1) |
| **Config** | Idea packets, hypothesis YAML |
| **Tests** | `tests/test_research_pipeline.py`, `tests/research_pipeline/test_generation_loop.py` |
| **Inputs** | Parsed hypothesis, registry model_id |
| **Outputs** | Candidate list + `feature_recipe_hash` (Phase 1), frozen manifest (Phase 13) |
| **Next** | Step 7 — registry |

**Warning:** `DEFAULT_PARAM_GRID` (threshold × holding × stop × take-profit) is **execution refinement**, not feature-family search. See [../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md) path table.

---

## 7. Model and hypothesis registry

| Field | Value |
|-------|-------|
| **Purpose** | Resolve HYP/PDF models, event bindings, composition |
| **Document** | [../workbench/MODEL_CATALOG.md](../workbench/MODEL_CATALOG.md), [../structural_models/PDF_MODELS.md](../structural_models/PDF_MODELS.md) |
| **Code** | `packages/features_engine/src/model_registry.py`, `hypotheses/registry.py`, `apps/workbench/src/registry/` |
| **Config** | `model_registry.yaml`, `apps/workbench/config/models.yaml`, `model_event_binding.yaml` |
| **Tests** | `tests/test_model_registry_slugs.py`, `tests/test_workbench/test_model_event_binding.py` |
| **Inputs** | Model slug, symbol, event_id |
| **Outputs** | Resolved model_id, composition, parameter bounds |
| **Next** | Step 8 — HftBacktest data validation |

---

## 8. HftBacktest Data Validation

| Field | Value |
|-------|-------|
| **Purpose** | Normalize and validate HftBacktest-compatible event data before any economics gate |
| **Document** | [../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md](../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md), [../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md) |
| **Code** | `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py`, `scripts/run_hftbacktest_only.py` |
| **Config** | normalized NPZ, initial snapshot, symbol/contract metadata, tick/lot/contract size |
| **Tests** | `tests/backtest_pipeline/test_hftbacktest_only_pipeline.py` |
| **Inputs** | HftBacktest event NPZ + initial snapshot |
| **Outputs** | `artifacts/hbt_runs/<run_id>/data_validation.json`, `data_manifest.json`, `normalized_input_manifest.json` |
| **Next** | Step 9 — HftBacktest strategy run |

```bash
python scripts/run_hftbacktest_only.py \
  --run-id hbt_smoke \
  --symbol MES \
  --contract MESH6 \
  --event-id CPI_2024_09_11_TIGHT \
  --data-npz data/hbt/normalized/MES/2024-09-11/CPI_2024_09_11_TIGHT_l3.npz \
  --initial-snapshot data/hbt/snapshots/MES/2024-09-11/CPI_2024_09_11_TIGHT_initial_snapshot.npz
```

VectorBT, Stage A survivor cells, and `screening_artifact.json` are inactive for
this active path. They remain historical diagnostics unless explicitly
re-enabled by the owner.

---

## 9. HftBacktest Strategy Run

| Field | Value |
|-------|-------|
| **Purpose** | Run one strategy through official HftBacktest semantics and write execution artifacts |
| **Document** | [../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md](../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md) |
| **Code** | `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py`, `scripts/run_hftbacktest_only.py` |
| **Config** | strategy id/params, latency model, fee model, exchange fill model, queue model |
| **Tests** | `tests/backtest_pipeline/test_hftbacktest_only_pipeline.py`, `tests/backtest_pipeline/test_hftbacktest_realism_hbt*.py` |
| **Inputs** | Valid HBT event data, initial snapshot, explicit run config |
| **Outputs** | `recorder_result.npz`, `stats_summary.json`, `orders.parquet`, `fills.parquet`, `latency_report.json`, `fill_quality_report.json`, `queue_diagnostics.json` |
| **Next** | Step 10 — post-HBT evaluation |

---

## 10. Post-HBT Evaluation

| Field | Value |
|-------|-------|
| **Purpose** | Evaluate only after HftBacktest output exists; do not pre-promote or pre-reject for economics |
| **Document** | [../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md](../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md), [../vault/BACKTESTER_CERTIFICATION.md](../vault/BACKTESTER_CERTIFICATION.md) |
| **Code** | `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py`, `apps/workbench/src/run/evidence_snapshot.py` |
| **Config** | promotion decision gates, robustness/certification tier policy |
| **Tests** | `tests/backtest_pipeline/test_hftbacktest_only_pipeline.py`, `tests/test_workbench/test_evidence_snapshot.py` |
| **Inputs** | `recorder_result.npz` + `stats_summary.json` from `artifacts/hbt_runs/<run_id>/` |
| **Outputs** | `promotion_decision.json`, Workbench `hbt_runs` source, Plan Drift Review receipts |
| **Next** | Step 11 — learning |

`promotion_decision.json` may be generated only after HftBacktest recorder and
stats artifacts exist.

---

## 11. Autonomous next-generation learning

| Field | Value |
|-------|-------|
| **Purpose** | Multi-gen loop: freeze → screen → robustness → HBT → memory → bounded next recipes |
| **Document** | [../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md](../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md) Phase 7, [../project/AUTORESEARCH_GAP_MATRIX.md](../project/AUTORESEARCH_GAP_MATRIX.md) |
| **Code** | `packages/research_pipeline/generation_loop.py`, `scripts/run_pipeline.py --autoresearch` |
| **Config** | `config/autoresearch/default.yaml` |
| **Tests** | `tests/research_pipeline/test_generation_loop.py`, `tests/test_workbench/test_self_learning_loop_contract.py` |
| **Inputs** | Thesis, event_id, prior generation summaries |
| **Outputs** | `research_cards/autoresearch/<campaign_id>/autoresearch_manifest.json`, `memory.jsonl` |
| **Next** | Step 12 — artifacts |

```bash
python scripts/run_pipeline.py --autoresearch --thesis "..." --event-id CPI_2024_09_11_TIGHT --no-llm --max-generations 3
```

**Current capability:** Refines execution parameters and bounded feature-family recipe variants (Phase 7). See `tests/research_pipeline/test_feature_family_e2e_smoke.py` for Phase 8 smoke.

---

## 12. Artifacts and cockpit visibility

| Field | Value |
|-------|-------|
| **Purpose** | Find every run output without repo grep |
| **Document** | [RUNTIME_CONTRACT.md](RUNTIME_CONTRACT.md), [../cockpit/REFRESH.md](../cockpit/REFRESH.md) |
| **Code** | `apps/workbench/src/artifacts/paths.py`, `apps/cockpit/backend/aggregate/` |
| **Config** | `research_cards/`, `artifacts/runs/` |
| **Tests** | `apps/cockpit/backend/tests/test_cockpit.py` |
| **Inputs** | Pipeline run IDs, campaign IDs |
| **Outputs** | Workbench cards, cockpit pipeline view, screening hashes |
| **Next** | Step 13 — unit tests |

| Artifact | Location |
|----------|----------|
| HBT active run | `artifacts/hbt_runs/<run_id>/` |
| Autoresearch campaign | `research_cards/autoresearch/<campaign_id>/` |
| Workbench robustness | `research_cards/workbench_runs/<campaign_id>/` |
| Legacy HBT campaign | `research_cards/autoresearch/.../hft_campaign/` or realism run dir |
| Feature-family status | `docs/project/FEATURE_FAMILY_STATUS_MANIFEST.yaml` |

---

## 13. Local unit tests

| Field | Value |
|-------|-------|
| **Purpose** | Fast gates before integration |
| **Document** | [../vault/BACKTESTER_CERTIFICATION.md](../vault/BACKTESTER_CERTIFICATION.md) T0 |
| **Code** | `tests/backtester_validation/fast/`, feature-plane and adapter tests |
| **Config** | `scripts/run_agent_verify.ps1` (180s budget) |
| **Tests** | Self |
| **Inputs** | Fresh checkout, `.venv` |
| **Outputs** | Pytest exit 0 |
| **Next** | Step 14 — smoke |

```bash
python -m pytest tests/backtester_validation/fast -q
python -m pytest tests/backtest_pipeline/test_feature_plane.py tests/research_pipeline/ -q
```

---

## 14. Smoke tests

| Field | Value |
|-------|-------|
| **Purpose** | One end-to-end path without paid compute |
| **Document** | [../vault/RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) |
| **Code** | `run_pipeline.py`, generation loop mocks |
| **Config** | Pilot `screening_scope`, fixture NPZ when available |
| **Tests** | `tests/research_pipeline/test_generation_loop.py`, `tests/research_pipeline/test_feature_family_e2e_smoke.py` |
| **Inputs** | `--no-llm`, tight event_id |
| **Outputs** | `final_report.json`, manifest with tested hashes |
| **Next** | Step 15 — paid gate |

---

## 15. HftBacktest Campaign Readiness

| Field | Value |
|-------|-------|
| **Purpose** | Block campaign/rented work until one local HBT-only run proves data, strategy, and artifact contracts |
| **Document** | [../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md](../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md), [../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md](../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md) |
| **Code** | `scripts/run_hftbacktest_only.py`, `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py` |
| **Config** | HBT run manifest, validated NPZ/snapshot manifests, explicit latency/fill/queue assumptions |
| **Tests** | `tests/backtest_pipeline/test_hftbacktest_only_pipeline.py`, `tests/backtest_pipeline/test_hftbacktest_realism_hbt*.py` |
| **Inputs** | Local HBT run artifact with recorder, stats, diagnostics, and post-HBT decision |
| **Outputs** | Campaign-ready HBT manifest set; no VectorBT or Stage A prerequisite |
| **Next** | Step 16 — full campaign |

Legacy VectorBT paid-screen runbooks remain historical diagnostics only.

---

## 16. Full campaign execution

| Field | Value |
|-------|-------|
| **Purpose** | Production-scale HBT campaign after local HBT-only contract is green |
| **Document** | [../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md](../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md), [../operations/VAST_HFT_CAMPAIGN.md](../operations/VAST_HFT_CAMPAIGN.md) |
| **Code** | `scripts/run_hftbacktest_only.py`, campaign runner adapters around HBT artifacts |
| **Config** | Campaign manifest, worker isolation spec |
| **Tests** | `tests/backtest_pipeline/hft_campaign/test_hft_campaign_integration.py` |
| **Inputs** | Validated manifests, checkpoint/resume |
| **Outputs** | Full HBT campaign artifact tree and Workbench `hbt_runs` visibility |
| **Next** | Update [FEATURE_FAMILY_STATUS_MANIFEST.yaml](../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml); append vault decision |

---

## Fresh checkout → validated campaign (command sequence)

```bash
# 0. Environment
cd hft3 && python -m venv .venv && .venv/Scripts/activate
pip install -r apps/workbench/requirements.txt

# 1. Invariants
python -m pytest tests/backtester_validation/fast -q

# 2. Event catalog
python -m economic_event_universe.cli validate

# 3. Single-shot HftBacktest-only run
python scripts/run_hftbacktest_only.py \
  --run-id hbt_smoke \
  --symbol MES \
  --contract MESH6 \
  --event-id CPI_2024_09_11_TIGHT \
  --data-npz <validated_hftbacktest_l3_npz> \
  --initial-snapshot <matching_initial_snapshot_npz>

# 4. Inspect active artifact
# artifacts/hbt_runs/<run_id>/
#   -> run_manifest.json, data_validation.json, recorder_result.npz,
#      stats_summary.json, promotion_decision.json

# 5. Optional autoresearch smoke (3 generations, no LLM)
python scripts/run_pipeline.py \
  --autoresearch --thesis "Fade spread blowout" \
  --event-id CPI_2024_09_11_TIGHT --no-llm --max-generations 3

# 6. Open Workbench active HBT run source
streamlit run apps/workbench/ui/app.py
```

---

## Retired paths (do not use for new work)

Listed in [FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md): `run_event_replay.py`, `run_event_universe.py`, `replay_matrix.py`, legacy `ReplaySession` discovery loop.
