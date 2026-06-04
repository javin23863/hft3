"""JSON Schema validation for data_layer packets."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import jsonschema

_PACKET_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=16)
def _load_schema(name: str) -> Dict[str, Any]:
    path = _PACKET_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_json(schema_name: str, obj: Any) -> List[str]:
    """Return human-readable validation errors (empty if valid)."""
    schema = _load_schema(schema_name)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
    out: List[str] = []
    for err in errors:
        path = ".".join(str(p) for p in err.path)
        out.append(f"{path}: {err.message}" if path else err.message)
    return out


def validate_aar_packet_in(packet: Dict[str, Any]) -> List[str]:
    errors = validate_json("schema_v1.json", packet)
    errors.extend(_aar_packet_invariants(packet))
    return errors


def _aar_packet_invariants(packet: Dict[str, Any]) -> List[str]:
    """Cross-field rules formerly in validate_packet_schema."""
    errors: List[str] = []
    lat = packet.get("latency_authority") or {}
    if lat.get("python_research_runtime_authoritative") is not False:
        errors.append("python_research_runtime_authoritative must be false")
    sim = packet.get("simulation_fidelity") or {}
    qts = sim.get("queue_tracker_status")
    if sim.get("cpp_replay_available"):
        if qts != "available":
            errors.append("queue_tracker_status must be available when cpp_replay_available")
    elif sim.get("cpp_stack_verified") and qts != "link_only":
        errors.append("queue_tracker_status must be link_only when cpp_stack_verified without replay")
    elif not sim.get("cpp_stack_verified") and not sim.get("cpp_replay_available") and qts != "stub_or_unverified":
        errors.append("queue_tracker_status must be stub_or_unverified when no C++ verify/replay")
    return errors


def validate_aar_packet_out(response: Dict[str, Any]) -> List[str]:
    return validate_json("schema_aar_response_v1.json", response)


def validate_pipeline_request(request: Dict[str, Any]) -> List[str]:
    return validate_json("schema_pipeline_request_v1.json", request)


def validate_pipeline_hypothesis_response(response: Dict[str, Any]) -> List[str]:
    return validate_json("schema_pipeline_hypothesis_response_v1.json", response)


def validate_pipeline_idea_set(response: Dict[str, Any]) -> List[str]:
    return validate_json("schema_pipeline_idea_set_v1.json", response)


def validate_pipeline_response(response: Dict[str, Any]) -> List[str]:
    return validate_json("schema_pipeline_response_v1.json", response)


CITATION_REQUIRED_SOURCE_TYPES = ("ONTOLOGY_EXTENSION", "PDF_CITATION", "SYMBOLIC_RESULT")
VALID_SOURCE_TYPES = (
    "ONTOLOGY_EXTENSION",
    "PDF_CITATION",
    "LATENCY_AUTHORITY_FIELD",
    "SYMBOLIC_RESULT",
)


def validate_kg_annotations_closed_claim(annotations: List[Dict[str, Any]]) -> List[str]:
    """Validate the LLM-emitted kg_annotations against the closed-claim contract.

    The contract (post phase 7) is: every annotation must declare a source_type
    in the closed enum, populate source_id/field/value, and — for any
    citation-bearing source — carry a cite with non-empty pdf/section/page.
    Returns a list of human-readable error messages (empty if all annotations
    are well-formed). The packet_runner drops annotations that fail this
    check before persisting the response.
    """
    errors: List[str] = []
    if not isinstance(annotations, list):
        return [f"kg_annotations must be a list, got {type(annotations).__name__}"]
    for i, ann in enumerate(annotations):
        prefix = f"kg_annotations[{i}]"
        if not isinstance(ann, dict):
            errors.append(f"{prefix}: must be a mapping, got {type(ann).__name__}")
            continue
        source_type = ann.get("source_type")
        if source_type not in VALID_SOURCE_TYPES:
            errors.append(f"{prefix}.source_type: must be one of {VALID_SOURCE_TYPES}, got {source_type!r}")
        source_id = ann.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"{prefix}.source_id: must be a non-empty string, got {source_id!r}")
        field = ann.get("field")
        if not isinstance(field, str) or not field.strip():
            errors.append(f"{prefix}.field: must be a non-empty string, got {field!r}")
        if "value" not in ann or ann["value"] is None:
            errors.append(f"{prefix}.value: must be non-null")
        if source_type in CITATION_REQUIRED_SOURCE_TYPES:
            cite = ann.get("cite")
            if not isinstance(cite, dict):
                errors.append(f"{prefix}.cite: required for source_type={source_type!r}, missing")
                continue
            pdf = str(cite.get("pdf", "")).strip()
            section = str(cite.get("section", "")).strip()
            page = cite.get("page")
            if not pdf:
                errors.append(f"{prefix}.cite.pdf: must be a non-empty string")
            if not section:
                errors.append(f"{prefix}.cite.section: must be a non-empty string")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                errors.append(f"{prefix}.cite.page: must be a positive int, got {page!r}")
    return errors


def drop_uncited_kg_annotations(annotations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only the annotations that pass the closed-claim check.

    Used by packet_runner to strip any LLM output that lacks a valid
    source_type/cite triplet. This keeps the persisted response
    fail-closed without raising on a single bad annotation.
    """
    if not isinstance(annotations, list):
        return []
    out: List[Dict[str, Any]] = []
    for ann in annotations:
        if not validate_kg_annotations_closed_claim([ann]):
            out.append(ann)
    return out
