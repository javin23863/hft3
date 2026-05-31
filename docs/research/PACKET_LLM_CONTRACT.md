# Packet-strict LLM contract

Authoritative I/O for workstation LLM lanes. All boundaries validated with `jsonschema` (draft-07).

## After-action (Gemma)

**In:** Envelope JSON (transport, not schema_v1):

```json
{
  "microstructure_aar_packet": { "...": "full schema_v1 — includes similar_prior_runs on disk" },
  "symbolic_result": { "passed": true, "violations": [] },
  "similar_prior_runs": []
}
```

`similar_prior_runs` is duplicated in the envelope when present (same data as on-disk packet after P1-1 fix).

**Out:** `after_action_response.json` per `schema_aar_response_v1.json` (canonical).

| Priority | Artifact | Role |
|----------|----------|------|
| 1 | `after_action_response.json` | Canonical LLM output |
| 2 | `after_action_annotations.json` | Legacy KG proposals (derived) |
| 3 | `after_action_report.md` | Human view (`narrative_md`) |

**Gates before LLM call:**

1. `validate_aar_packet_in` (jsonschema + cross-field invariants)
2. `validate_connector` (OpenFoundry)
3. `pdf_citations_complete == true`
4. `symbolic.passed == true` (else deterministic symbolic-only narrative)

**Env:** `HFT3_OLLAMA_MODEL=gemma4:31b-cloud`, `HFT3_OLLAMA_TIMEOUT_S=600`

## Research pipeline (GLM)

**Hypothesis parse (GLM):** `schema_pipeline_hypothesis_response_v1.json` via `run_llm_on_hypothesis_request` when `parse_hypothesis(..., pipeline_request=..., repo_root=...)`.

**Run artifacts:** `request_packet.json` / `response_packet.json` per pipeline request/response schemas.

**Env:** `HFT3_PIPELINE_LLM_MODEL=glm-5.1:cloud`

## Runtime schema mirror

Sync from packet dir to `runtime/schemas/`:

```powershell
.\scripts\sync_runtime_schemas.ps1
```

## Code entrypoints

- `packages/data_layer/llm/packet_runner.py`
- `packages/data_layer/packet/validate.py`
- `packages/research_pipeline/packets.py`
- `packages/data_layer/pipeline/after_action.py`
