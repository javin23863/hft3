# OpenFoundry Ontology Hardening

This doc records the fail-closed path that prevents hft3 LLM lanes from making uncited ontology claims.

## Scope

The hardening applies to the workstation-only LLM lanes:

| Lane | Entry | Guardrail |
|------|-------|-----------|
| After-action | `data_layer.llm.packet_runner.run_llm_on_aar_packet` | Closed-claim `kg_annotations[]` + deterministic `narrative_md` |
| Autoresearch pipeline | `research_pipeline.llm.generate_json` | Connector/vendor/schema checks before persistence |
| Model-development hypothesis parse | `data_layer.llm.packet_runner.run_llm_on_hypothesis_request` | Connector/vendor/schema checks before parsing |

The symbolic gate follows the AlphaGeometry pattern: deterministic verifier before LLM. It does not run AlphaGeometry on order books.

## ODL Citation Rule

The OpenFoundry ODL parser has a hard-coded directive allowlist. Unknown directives such as `@pdf_cite(...)` are silently dropped, so inline PDF citations are not reliable.

Accepted rule:

- ODL files live under `integrations/openfoundry/domain-packs/hft3/schema/*.odl`.
- Every connector-declared extension has a sidecar under `integrations/openfoundry/domain-packs/hft3/citations/<Type>.yaml`.
- `validate_ontology_citations()` fails if any sidecar is missing, malformed, uncited, or cites a PDF that is not on disk.

The citation table is in `docs/research/ONTOLOGY_CITATIONS.md` and mirrored in `docs/references/MANIFEST.md`.

## Symbolic Grounding

`packages/data_layer/symbolic/latency_invariants.py` returns a grounded result:

```json
{
  "passed": false,
  "violations": ["trade[0]: market_data_receive_ts before market_data_exchange_ts"],
  "grounded": true,
  "violation_cites": [
    {
      "message": "trade[0]: market_data_receive_ts before market_data_exchange_ts",
      "cite": {
        "pdf": "chicago_cme_microstructure_mathematical_model.pdf",
        "section": "§1 Information set",
        "page": 1
      }
    }
  ]
}
```

The legacy `violations: list[str]` field remains for backward compatibility. `violation_cites[]` is the grounded claim surface.

## Closed-Claim LLM Contract

The LLM is not allowed to write free-form KG proposals. It may only emit closed-claim annotations:

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

Allowed `source_type` values:

| source_type | Meaning | Cite required |
|-------------|---------|---------------|
| `ONTOLOGY_EXTENSION` | Claim grounded in hft3 ODL sidecar | Yes |
| `PDF_CITATION` | Claim grounded directly in a PDF citation | Yes |
| `LATENCY_AUTHORITY_FIELD` | Runtime packet value from `latency_authority` | No |
| `SYMBOLIC_RESULT` | Claim grounded in symbolic violation cite | Yes |

Invalid annotations are dropped by `drop_uncited_kg_annotations()` before persistence.

## Deterministic Narrative

`narrative_md` is no longer an LLM-authored field. On the successful AAR path, `packet_runner` replaces it with `render_deterministic_narrative(packet, symbolic, kg_annotations)` from `packages/data_layer/llm/narrative_renderer.py`.

Skipped paths still use deterministic fixed strings or `build_symbolic_narrative()`.

## Fail-Closed Statuses

Connector failures now distinguish ontology problems:

| llm_status | Trigger |
|------------|---------|
| `skipped_uncited_ontology` | Missing/malformed ODL citation sidecar or empty claims |
| `skipped_broken_pdf_cite` | Sidecar cites a PDF that is not present under `docs/references/` |
| `skipped_connector` | Generic OpenFoundry/vendor/lock failure |

## Verification Surface

Relevant tests:

| Test file | Coverage |
|-----------|----------|
| `tests/test_data_layer/test_grounded_symbolic_gate.py` | Every symbolic violation has a PDF cite |
| `tests/test_data_layer/test_ontology_citations.py` | ODL sidecars exist and validate |
| `tests/test_data_layer/test_connector_gate_classification.py` | Ontology failures map to specific `llm_status` values |
| `tests/test_data_layer/test_closed_claim_validator.py` | Closed-claim annotation validator and drop helper |
| `tests/test_data_layer/test_narrative_renderer.py` | Deterministic `narrative_md` renderer |

Last targeted run during implementation: `python -m pytest tests/test_data_layer/` -> 72 passed, 1 skipped.
