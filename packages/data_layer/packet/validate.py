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


def validate_pipeline_response(response: Dict[str, Any]) -> List[str]:
    return validate_json("schema_pipeline_response_v1.json", response)
