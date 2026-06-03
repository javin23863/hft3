# Packet-Strict LLM Contract

Authoritative I/O for workstation LLM lanes. All boundaries are validated with `jsonschema` draft-07 plus explicit hft3 fail-closed checks.

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
