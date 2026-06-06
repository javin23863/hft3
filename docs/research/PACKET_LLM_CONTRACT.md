# Packet-Strict LLM Contract

Authoritative I/O for workstation LLM lanes. All boundaries are validated with `jsonschema` draft-07 plus explicit hft3 fail-closed checks.

## Research And Model-Development LLM Readiness Map

Provenance note:

- Audit date: 2026-06-06
- Branch: `codex/workbench-runtime-sync`
- HEAD: `1c854d0abf25`
- Scope: additive source-of-truth readiness map; no runtime behavior change; no graph commands.

This readiness map is source-of-truth material for LLM discoverability. It describes current wiring status only; it does not grant any LLM lane production authority.

### Current Verdicts

- Research LLM verdict: partially wired, not production-ready.
- Model Development LLM verdict: partially wired, not production-ready.
- Both lanes are advisory only. No LLM lane has promotion authority, deploy authority, live-routing authority, or permission to override deterministic replay, schema, registry, validation, workbench, VectorBT, CHI404, Rithmic, or promotion gates.

### Production Blockers

- Research blocker: the non-idea research path in `scripts/run_pipeline.py` / `packages/research_pipeline/` can still artifact-deploy best failing candidates; until this is fail-closed, those artifacts must be treated as research-only and must carry no deploy semantics.
- Research blocker: hypothesis parse `feature_list` is only a string-array schema field in `packages/data_layer/packet/schema_pipeline_hypothesis_response_v1.json` and is not universally validated against `packages/features_engine/src/features/registry.py`; unvalidated values can expand model families.
- Research blocker: analyst chat in `apps/workbench/ui/analyst_panel.py` remains direct/free-form LLM, not packet/schema strict.
- Research blocker: live GPT-5.5 production smoke wiring is present, but there is no discoverably green current result tied to this contract.
- Research blocker: `packages/research_pipeline/requirements.txt` lacks `jsonschema` while docs suggest standalone CLI install.
- Research blocker: prior AAR artifacts compacted into `review_memory[]` are advisory and are not revalidated at read time.
- Research blocker: heuristic fallback remains active when the LLM is unavailable or schema-rejected; production policy must decide whether that is acceptable.
- Model-development blocker: generated model card artifacts from `apps/workbench/src/run/campaign_runner.py` may still contain generic placeholders such as `baseline_model: "not_observed"` and `uplift_metric: "not_observed"`.
- Model-development blocker: `packages/features_engine/src/features/registry.py` accepts aliases, so canonical-only `feature_id` enforcement is not universal.
- Model-development blocker: the campaign card writer may persist `model_card.json` before full `validate_agent_contract` / schema / registry validation.
- Model-development blocker: promotion provenance checks in `packages/hft3/validation/certification_registry.py` require card paths and IDs, but may not fully revalidate `MODEL_CARD` schema and feature-registry/model-kind eligibility.
- Model-development blocker: feature fabric manifests in `apps/workbench/src/run/feature_fabric.py` distinguish catalog eligibility from observed model usage; accepted feature does not prove model consumed the feature.
- Model-development blocker: hypothesis parse `feature_list` remains string-array only and not feature-registry validated.

### Unblock Conditions

- Block non-idea failing artifact deployment, or mark it research-only with no deploy semantics.
- Force hypothesis and model features through canonical registered `feature_id`s at all LLM boundaries, including hypothesis parse, idea set, model card, validation card, and promotion provenance boundaries.
- Make analyst chat explicitly non-contract/advisory or convert it to packet/schema strict I/O.
- Reject generic placeholders in model-card and promotion provenance gates where production eligibility is claimed.
- Validate emitted model and validation cards against `docs/schemas/MODEL_CARD.schema.json`, `docs/schemas/VALIDATION_CARD.schema.json`, and the feature registry before persistence and before promotion.
- Produce bounded green LLM/packet verification, including live GPT-5.5 smoke only when the environment explicitly enables it, with command, exit code, output tail, and artifact paths recorded.

### Discovery Notes

- Research pipeline LLM entrypoints are documented below under Research Pipeline Lane, Machine Idea-Set Lane, and Research Decision Packet Lane.
- Model-development hypothesis parse enters through `data_layer.llm.packet_runner.run_llm_on_hypothesis_request` and validates the packet shape through `validate_pipeline_hypothesis_response`; that does not by itself prove canonical feature eligibility.
- Agent contract validation lives in `packages/data_layer/packet/agent_contracts.py`; production readiness requires schema validation plus registry validation at persistence and promotion time, not only at later review time.
- Workbench cards and feature fabric material are emitted through `apps/workbench/src/run/campaign_runner.py`, `apps/workbench/src/run/feature_fabric.py`, and surfaced through `apps/workbench/src/run/evidence_snapshot.py`.

## After-Action Lane

Runtime entry: `data_layer.llm.packet_runner.run_llm_on_aar_packet`.

Default model: `gpt-5.5` with `xhigh` reasoning.

Environment:

```powershell
set HFT3_AAR_LLM_MODEL=gpt-5.5
set HFT3_LLM_REASONING_EFFORT=xhigh
set HFT3_LLM_TIMEOUT_S=600
```

Input envelope:

```json
{
  "microstructure_aar_packet": { "...": "full schema_v1 packet" },
  "symbolic_result": { "passed": true, "violations": [], "violation_cites": [] },
  "similar_prior_runs": []
}
```

Output artifact: `after_action_response.json` per `schema_aar_response_v1.json`.

## AAR Gates Before LLM Call

The LLM is skipped unless all gates pass:

1. `validate_aar_packet_in(packet)` passes JSON schema and cross-field invariants.
2. `pdf_citations_complete == true`.
3. `validate_connector(repo_root)` loads the OpenFoundry connector and pinned vendor lock.
4. `assert_connector_valid(...)` confirms vendor/core pack and ontology citations.
5. `symbolic.passed == true`.

Failure statuses:

| llm_status | Trigger |
|------------|---------|
| `skipped_pdf` | Packet PDF citations incomplete |
| `skipped_uncited_ontology` | ODL sidecar missing/malformed/uncited |
| `skipped_broken_pdf_cite` | ODL sidecar cites a missing PDF |
| `skipped_connector` | Generic OpenFoundry/vendor failure |
| `skipped_symbolic` | Symbolic invariant failed |

## Closed-Claim kg_annotations

`kg_annotations[]` is a closed-claim array. The old `{from,to,relation,scope}` proposal shape is not valid.

Each LLM lane has its own closed-world packet contract. This contract is not the OpenFoundry operational ontology and not the slow relationship-reasoning layer; it is the boundary that prevents lane-specific LLM output from inventing uncited graph claims.

Each item:

```json
{
  "source_type": "ONTOLOGY_EXTENSION",
  "source_id": "MarkedMicroEvent",
  "field": "kind",
  "value": "ADD",
  "cite": {
    "pdf": "chicago_cme_microstructure_mathematical_model.pdf",
    "section": "§4 MBO Marked Point Process",
    "page": 2
  }
}
```

Allowed sources:

| source_type | Cite required | Valid source_id examples |
|-------------|---------------|--------------------------|
| `ONTOLOGY_EXTENSION` | Yes | `MarkedMicroEvent`, `LatencyChainUs`, `EventContext` |
| `PDF_CITATION` | Yes | `event_context`, `latency_authority`, `structural_models` |
| `LATENCY_AUTHORITY_FIELD` | No | `latency_authority` |
| `SYMBOLIC_RESULT` | Yes | Symbolic violation message |

Validation functions:

- `validate_kg_annotations_closed_claim(annotations)` returns human-readable errors.
- `drop_uncited_kg_annotations(annotations)` keeps only valid closed-claim annotations.
- `data_layer.pipeline.after_action._valid_kg_annotations()` re-filters before KG persistence.

## Relationship-Reasoning Boundary

`packages/research_pipeline/relationship_reasoning/` models slow/offline relationship candidates across defined source contexts. Each evidence item must name a defined `RelationshipDataSource` and canonical `source_ref`; at least one evidence item must be an empirical offline source. `world_event` evidence is valid only through cached `GDELT_WORLD_EVENTS` source refs. Those candidates are not packet `kg_annotations[]`, not LLM prose, and not authoritative KG/OpenFoundry writes.

If a future packet summarizes a validated relationship candidate, it must still emit a valid closed-claim `kg_annotations[]` item with an allowed `source_type` and required citation. The relationship layer cannot bypass this contract.

## Deterministic narrative_md

`narrative_md` is not LLM-authored. On successful AAR responses, `packet_runner` overwrites any LLM-provided prose with:

```python
render_deterministic_narrative(packet, symbolic, kg_annotations)
```

Renderer path: `packages/data_layer/llm/narrative_renderer.py`.

Skipped paths use deterministic fixed messages or `build_symbolic_narrative()`.

## Research Pipeline Lane

Runtime entry: `research_pipeline.llm.generate_json`.

Default model: `gpt-5.5`.

Artifacts:

| Artifact | Schema |
|----------|--------|
| `request_packet.json` | `schema_pipeline_request_v1.json` |
| `response_packet.json` | `schema_pipeline_response_v1.json` |

Connector statuses include `skipped_uncited_ontology` and `skipped_broken_pdf_cite` so ontology failures remain visible even outside AAR.

## Model-Development Hypothesis Parse

Runtime entry: `data_layer.llm.packet_runner.run_llm_on_hypothesis_request`.

Schema: `schema_pipeline_hypothesis_response_v1.json`.

The hypothesis lane does not emit `kg_annotations[]` or `narrative_md`; it still shares the same connector gate and `llm_status` ontology failure values.

## Machine Idea-Set Lane

Runtime entry: `data_layer.llm.packet_runner.run_llm_on_idea_generation_request`.

Schema: `schema_pipeline_idea_set_v1.json`.

This lane is opt-in (`scripts/run_pipeline.py --idea-set`) and emits compact machine-review packets only: IDs, enums, numeric ranges, ref tables, rank inputs, and status codes. It must not emit markdown, narrative fields, promotion claims, or human review text.

Idea records are non-authoritative queue inputs. Static validation may move an idea from `proposed` to `static_reject` or `queued_for_test`; only existing VectorBT/workbench/promotion-gate evaluation may move it to `tested_fail` or `tested_pass`. Server-side deterministic ordering and fair candidate allocation control test order; LLM-provided `rank_inputs` are packet telemetry only and never mark an idea as good.

Review memory is advisory and bounded. Prior AAR/KG artifacts may be compacted into `review_memory[]` facts, but those facts cannot set parameters, promote candidates, skip tests, or override gate results. Idea `param_ranges` are validated for packet integrity, then clamped to deterministic pipeline ranges before candidate generation.

## Research Decision Packet Lane

Schema: `schema_research_decision_packet_v1.json`.

The Research Decision Packet is an offline, advisory-only input contract for future ontology-governed research. It carries market, event, knowledge, ontology, validation, risk-handoff, and audit state in one closed JSON object. It has no promotion authority and contains no live routing, CHI404, Rithmic, order-submit, or deploy fields.

Candidate research questions must name required ontology variables and evidence sources. `ontology_state` must carry allowed entities, variables, and formulas, and `audit` must include `source_registry_version`, `ontology_version`, and `code_commit`.

## Runtime Schema Mirror

Packet schemas must stay mirrored under `runtime/schemas/`:

```powershell
.\scripts\sync_runtime_schemas.ps1
```

Guard test: `tests/test_data_layer/test_sync_runtime_schemas.py`.

## Code Entrypoints

| Path | Role |
|------|------|
| `packages/data_layer/llm/packet_runner.py` | LLM gate, closed-claim postprocess, deterministic narrative wiring |
| `packages/data_layer/llm/prompts.py` | Closed-claim-only LLM prompt |
| `packages/data_layer/llm/narrative_renderer.py` | Deterministic markdown renderer |
| `packages/data_layer/packet/validate.py` | JSON schema + closed-claim validator |
| `packages/data_layer/openfoundry_bridge.py` | Connector/vendor/ontology sidecar validation |
| `packages/data_layer/pipeline/after_action.py` | AAR artifact persistence + KG annotation re-filter |
