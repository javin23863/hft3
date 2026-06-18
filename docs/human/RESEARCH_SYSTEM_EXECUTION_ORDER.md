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
8. VectorBT screening
9. Robustness gates
10. HftBacktest realism
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
| **Next** | Step 8 — VectorBT |

---

## 8. VectorBT screening

| Field | Value |
|-------|-------|
| **Purpose** | Fast screen with honest feature-plane labeling; emit terminal `screening_artifact.json` |
| **Document** | [../project/VECTORBT_SCREENING_ENGINE_SPEC.md](../project/VECTORBT_SCREENING_ENGINE_SPEC.md), [../human/VECTORBT_PIPELINE.md](VECTORBT_PIPELINE.md) |
| **Code** | `packages/backtest_pipeline/src/vectorbt_adapter.py`, `scripts/run_pipeline.py --vectorbt` |
| **Config** | `bar_construction_id`, `screening_scope`, rust engine requirement for broad scope |
| **Tests** | `tests/test_vectorbt_adapter.py`, `tests/test_vectorbt_paid_screen_gate.py` |
| **Inputs** | Frozen candidate manifest, OHLCV or fs_v1 row loop data |
| **Outputs** | `research_cards/pipeline_runs/<run_id>/screening_artifact.json`, `feature_plane_status` |
| **Next** | Step 9 — robustness |

```bash
python scripts/run_pipeline.py --thesis "..." --event-id CPI_2024_09_11_TIGHT --vectorbt --no-llm
```

**Default path status:** auto-selects `fs_v1_row_loop_from_feature_store` when feature-store NPZ exists for `(symbol, event_id)`; otherwise falls back to `bar_stub_research_only` (Phase 5).

---

## 9. Robustness gates

| Field | Value |
|-------|-------|
| **Purpose** | Walk-forward / WFC validation on **frozen** screened parameters |
| **Document** | [../project/ROBUSTNESS_TESTING_SPEC.md](../project/ROBUSTNESS_TESTING_SPEC.md) |
| **Code** | `packages/backtest_pipeline/src/robustness_bridge.py`, `apps/workbench/src/run/campaign_runner.py` |
| **Config** | `walk_forward.yaml`, `wfc_gate.yaml` |
| **Tests** | `tests/backtest_pipeline/test_robustness_bridge.py`, workbench WFC tests |
| **Inputs** | Promoted candidates from screening artifact |
| **Outputs** | Campaign dir `summary.json`, WFC status PASS/FAIL |
| **Next** | Step 10 — HftBacktest |

---

## 10. HftBacktest realism

| Field | Value |
|-------|-------|
| **Purpose** | Queue, latency, fill realism; same recipe identity as VectorBT |
| **Document** | [../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md), [../project/VECTORBT_TO_HFTBACKTEST_HANDOFF.md](../project/VECTORBT_TO_HFTBACKTEST_HANDOFF.md) |
| **Code** | `packages/backtest_pipeline/src/hftbacktest_realism.py`, `hft_campaign/runner.py` |
| **Config** | Validated NPZ, latency model JSON, fill queue model, upstream ref |
| **Tests** | `tests/backtest_pipeline/hft_campaign/`, `tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py` |
| **Inputs** | Terminal screening artifact (rust pass), native hot-path evidence |
| **Outputs** | HBT-0..4 artifacts, campaign manifests |
| **Next** | Step 11 — learning |

```bash
python scripts/run_pipeline.py ... --vectorbt --hftbacktest-realism \
  --hftbacktest-data-npz <npz> --hftbacktest-latency-model <json> \
  --hftbacktest-fill-queue-model <json> --hftbacktest-upstream-ref v2.4.2 \
  --native-hot-path-evidence <path>#sha256:<digest>
```

**Phase 6 gate:** `vectorbt feature_recipe_hash == hftbacktest feature_recipe_hash` (to be implemented).

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

**Current limitation:** Refines execution parameters; feature-family recipe generation is Phase 7 work.

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
| VectorBT screen | `research_cards/pipeline_runs/<run_id>/screening_artifact.json` |
| Autoresearch campaign | `research_cards/autoresearch/<campaign_id>/` |
| Workbench robustness | `research_cards/workbench_runs/<campaign_id>/` |
| HBT campaign | `research_cards/autoresearch/.../hft_campaign/` or realism run dir |
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
| **Tests** | `tests/research_pipeline/test_generation_loop.py` (3-gen mock) |
| **Inputs** | `--no-llm`, tight event_id |
| **Outputs** | `final_report.json`, manifest with tested hashes |
| **Next** | Step 15 — paid gate |

---

## 15. Paid-compute readiness

| Field | Value |
|-------|-------|
| **Purpose** | Block rented workers until pilot proves feature-family coverage |
| **Document** | [../project/VBT_PAID_SCREEN_RUNBOOK.md](../project/VBT_PAID_SCREEN_RUNBOOK.md), [../project/VBT_PAID_SCREEN_UNIT_SCOPE.md](../project/VBT_PAID_SCREEN_UNIT_SCOPE.md) |
| **Code** | `scripts/run_vectorbt_paid_screen.py` |
| **Config** | `paid_screen_gate` in [FEATURE_FAMILY_STATUS_MANIFEST.yaml](../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml) |
| **Tests** | `tests/test_vectorbt_paid_screen_gate.py` |
| **Inputs** | Pilot artifact with all family statuses + PIT proof |
| **Outputs** | Paid unit JSONL, per-unit screening artifacts |
| **Next** | Step 16 — full campaign |

**Gate:** `paid_screen_gate.allowed: false` until Phase 9 pilot passes.

---

## 16. Full campaign execution

| Field | Value |
|-------|-------|
| **Purpose** | Production-scale screen → robustness → HBT campaign |
| **Document** | [../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md](../project/HFTBACKTEST_CAMPAIGN_ARCHITECTURE.md), [../operations/VAST_HFT_CAMPAIGN.md](../operations/VAST_HFT_CAMPAIGN.md) |
| **Code** | `scripts/hft_run_campaign.py`, `hft_campaign/runner.py`, paid VectorBT |
| **Config** | Campaign manifest, worker isolation spec |
| **Tests** | `tests/backtest_pipeline/hft_campaign/test_hft_campaign_integration.py` |
| **Inputs** | Validated manifests, checkpoint/resume |
| **Outputs** | Full campaign artifact tree under `research_cards/` |
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

# 3. Single-shot VectorBT screen (fs_v1 when feature store present; else bar stub)
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise on MES" \
  --event-id CPI_2024_09_11_TIGHT \
  --symbol MES \
  --vectorbt --no-llm

# 4. Inspect feature plane on artifact
# research_cards/pipeline_runs/<run_id>/screening_artifact.json
#   → feature_plane_status, feature_usage_manifest

# 5. Optional autoresearch smoke (3 generations, no LLM)
python scripts/run_pipeline.py \
  --autoresearch --thesis "Fade spread blowout" \
  --event-id CPI_2024_09_11_TIGHT --no-llm --max-generations 3

# 6. HBT handoff (when validated NPZ + latency models exist)
python scripts/run_pipeline.py ... --vectorbt --hftbacktest-realism \
  --hftbacktest-data-npz <npz> \
  --hftbacktest-latency-model <latency.json> \
  --hftbacktest-fill-queue-model <fill.json> \
  --hftbacktest-upstream-ref v2.4.2 \
  --native-hot-path-evidence <evidence>#sha256:<digest>

# 7. Do NOT run paid screen until FEATURE_FAMILY_STATUS_MANIFEST paid_screen_gate allows
```

---

## Retired paths (do not use for new work)

Listed in [FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md): `run_event_replay.py`, `run_event_universe.py`, `replay_matrix.py`, legacy `ReplaySession` discovery loop.
