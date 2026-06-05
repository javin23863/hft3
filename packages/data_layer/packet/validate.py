"""JSON Schema validation for data_layer packets."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from numbers import Real
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


MBO_SOURCE_DOC_ID = "docs/research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md"
REQUIRED_MBO_FEATURE_FAMILY_CODES = (
    "static_depth",
    "microprice",
    "dynamic_ofi",
    "queue_position",
    "age_survival",
    "cancellation",
    "replenishment",
    "iceberg",
    "fleeting_liquidity",
    "entropy",
    "book_shape_geometry",
    "hawkes_intensity",
    "queue_reactive",
    "cross_asset_ofi",
    "adverse_selection",
    "latency_haircut",
)


def validate_mbo_feature_packet(packet: Dict[str, Any]) -> List[str]:
    errors = validate_json("schema_mbo_feature_packet_v1.json", packet)
    errors.extend(_mbo_feature_packet_invariants(packet))
    return errors


def _mbo_feature_packet_invariants(packet: Dict[str, Any]) -> List[str]:
    if not isinstance(packet, dict):
        return []

    errors: List[str] = []
    _append_non_finite_number_errors(errors, packet, "")

    timestamp_ns = packet.get("timestamp_ns")
    receive_timestamp_ns = packet.get("receive_timestamp_ns")
    if _is_int(timestamp_ns) and _is_int(receive_timestamp_ns):
        if receive_timestamp_ns < timestamp_ns:
            errors.append("receive_timestamp_ns must be >= timestamp_ns")
        else:
            latency_budget_us = packet.get("latency_budget_us")
            if _is_finite_real(latency_budget_us):
                observed_latency_us = (receive_timestamp_ns - timestamp_ns) / 1000.0
                if observed_latency_us > float(latency_budget_us):
                    errors.append(
                        "receive_timestamp_ns - timestamp_ns exceeds latency_budget_us"
                    )

    flow = _dict_value(packet, "flow")
    mlofi_vector = flow.get("mlofi_vector")
    mlofi_level_count = flow.get("mlofi_level_count")
    if isinstance(mlofi_vector, list) and _is_int(mlofi_level_count):
        if len(mlofi_vector) != mlofi_level_count:
            errors.append("flow.mlofi_level_count must equal len(flow.mlofi_vector)")

    audit = _dict_value(packet, "audit")
    source_doc_ids = audit.get("source_doc_ids")
    if isinstance(source_doc_ids, list) and MBO_SOURCE_DOC_ID not in source_doc_ids:
        errors.append(f"audit.source_doc_ids must include {MBO_SOURCE_DOC_ID}")

    feature_family_codes = audit.get("feature_family_codes")
    if isinstance(feature_family_codes, list):
        present = {code for code in feature_family_codes if isinstance(code, str)}
        missing = sorted(set(REQUIRED_MBO_FEATURE_FAMILY_CODES).difference(present))
        if missing:
            errors.append(
                "audit.feature_family_codes missing required MBO families: "
                + ", ".join(missing)
            )

    return errors


def _append_non_finite_number_errors(errors: List[str], value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _append_non_finite_number_errors(errors, child, child_path)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            _append_non_finite_number_errors(errors, child, child_path)
        return
    if _is_real(value) and not _is_finite_real(value):
        errors.append(f"{path}: must be finite")


def _is_real(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _is_finite_real(value: Any) -> bool:
    if not _is_real(value):
        return False
    if isinstance(value, int):
        return True
    return math.isfinite(float(value))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


REQUIRED_RESEARCH_FORBIDDEN_ACTIONS = (
    "invent_variable",
    "invent_formula",
    "infer_trade_direction_from_news_only",
    "skip_validation",
    "modify_hypothesis_after_failure_without_audit",
    "promote_without_validation",
)

REQUIRED_RESEARCH_VALIDATION_TESTS = (
    "source_traceability",
    "ontology_membership",
    "math_consistency",
    "data_quality",
    "point_in_time_safety",
    "walk_forward",
    "out_of_sample",
    "multiple_testing_correction",
    "regime_break",
    "liquidity_conditioning",
)


def validate_research_decision_packet(packet: Dict[str, Any]) -> List[str]:
    errors = validate_json("schema_research_decision_packet_v1.json", packet)
    errors.extend(_research_decision_packet_invariants(packet))
    return errors


def _research_decision_packet_invariants(packet: Dict[str, Any]) -> List[str]:
    if not isinstance(packet, dict):
        return []

    errors: List[str] = []
    decision_context = _dict_value(packet, "decision_context")
    forbidden_actions = decision_context.get("forbidden_actions")
    if isinstance(forbidden_actions, list):
        present = {action for action in forbidden_actions if isinstance(action, str)}
        missing = sorted(set(REQUIRED_RESEARCH_FORBIDDEN_ACTIONS).difference(present))
        if missing:
            errors.append(
                "decision_context.forbidden_actions missing required guardrails: "
                + ", ".join(missing)
            )

    validation_requirements = _dict_value(packet, "validation_requirements")
    required_tests = validation_requirements.get("required_tests")
    if isinstance(required_tests, list):
        present = {test_name for test_name in required_tests if isinstance(test_name, str)}
        missing = sorted(set(REQUIRED_RESEARCH_VALIDATION_TESTS).difference(present))
        if missing:
            errors.append(
                "validation_requirements.required_tests missing required guardrails: "
                + ", ".join(missing)
            )

    ontology_state = _dict_value(packet, "ontology_state")
    knowledge_state = _dict_value(packet, "knowledge_state")
    allowed_variable_ids = _ids_from_records(ontology_state.get("allowed_variables"), "variable_id")
    allowed_entity_ids = _ids_from_records(ontology_state.get("allowed_entities"), "entity_id")
    ontology_formula_ids = _ids_from_records(ontology_state.get("allowed_formulas"), "formula_id")
    knowledge_formula_ids = _ids_from_records(knowledge_state.get("formulas_available"), "formula_id")
    approved_source_ids = _ids_from_records(knowledge_state.get("approved_sources_retrieved"), "source_id")
    referenced_source_ids: set[str] = set()
    source_id_record_sets = (
        ("ontology_state.allowed_entities", ontology_state.get("allowed_entities")),
        ("ontology_state.allowed_variables", ontology_state.get("allowed_variables")),
        ("ontology_state.allowed_formulas", ontology_state.get("allowed_formulas")),
        ("ontology_state.allowed_transformations", ontology_state.get("allowed_transformations")),
        ("knowledge_state.formulas_available", knowledge_state.get("formulas_available")),
        ("knowledge_state.concepts_available", knowledge_state.get("concepts_available")),
    )
    for path, records in source_id_record_sets:
        referenced_source_ids.update(_source_ids_from_records(records))
        _validate_record_ids(
            errors,
            path,
            records,
            "source_ids",
            approved_source_ids,
            "not in knowledge_state.approved_sources_retrieved",
        )

    for path, records in (
        ("ontology_state.allowed_formulas", ontology_state.get("allowed_formulas")),
        ("knowledge_state.formulas_available", knowledge_state.get("formulas_available")),
    ):
        _validate_record_ids(
            errors,
            path,
            records,
            "variable_ids",
            allowed_variable_ids,
            "not in ontology_state.allowed_variables",
        )

    transformations = ontology_state.get("allowed_transformations")
    for field in ("input_variable_ids", "output_variable_ids"):
        _validate_record_ids(
            errors,
            "ontology_state.allowed_transformations",
            transformations,
            field,
            allowed_variable_ids,
            "not in ontology_state.allowed_variables",
        )

    _validate_cross_asset_feature_refs(
        errors,
        packet,
        allowed_variable_ids,
        ontology_formula_ids,
        knowledge_formula_ids,
        approved_source_ids,
        referenced_source_ids,
    )

    questions = packet.get("candidate_research_questions")
    if isinstance(questions, list):
        for idx, question in enumerate(questions):
            if not isinstance(question, dict):
                continue
            prefix = f"candidate_research_questions[{idx}]"
            _append_unknown_ids(
                errors,
                prefix,
                "required_variables",
                question.get("required_variables"),
                allowed_variable_ids,
                "not in ontology_state.allowed_variables",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "required_formulas",
                question.get("required_formulas"),
                ontology_formula_ids,
                "not in ontology_state.allowed_formulas",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "required_formulas",
                question.get("required_formulas"),
                knowledge_formula_ids,
                "not in knowledge_state.formulas_available",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "required_entities",
                question.get("required_entities"),
                allowed_entity_ids,
                "not in ontology_state.allowed_entities",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "required_sources",
                question.get("required_sources"),
                approved_source_ids,
                "not in knowledge_state.approved_sources_retrieved",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "required_sources",
                question.get("required_sources"),
                referenced_source_ids,
                "not referenced by ontology/formula/source refs",
            )
    return errors


def _dict_value(obj: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _ids_from_records(records: Any, key: str) -> set[str]:
    if not isinstance(records, list):
        return set()
    out: set[str] = set()
    for record in records:
        if isinstance(record, dict) and isinstance(record.get(key), str):
            out.add(record[key])
    return out


def _source_ids_from_records(records: Any) -> set[str]:
    if not isinstance(records, list):
        return set()
    out: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        source_ids = record.get("source_ids")
        if isinstance(source_ids, list):
            out.update(source_id for source_id in source_ids if isinstance(source_id, str))
    return out


def _validate_record_ids(
    errors: List[str],
    path: str,
    records: Any,
    field: str,
    allowed: set[str],
    reason: str,
) -> None:
    if not isinstance(records, list):
        return
    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        _append_unknown_ids(errors, f"{path}[{idx}]", field, record.get(field), allowed, reason)


def _validate_cross_asset_feature_refs(
    errors: List[str],
    packet: Dict[str, Any],
    allowed_variable_ids: set[str],
    ontology_formula_ids: set[str],
    knowledge_formula_ids: set[str],
    approved_source_ids: set[str],
    referenced_source_ids: set[str],
) -> None:
    market_state = _dict_value(packet, "market_state")
    cross_asset_features = market_state.get("cross_asset_features")
    if not isinstance(cross_asset_features, dict):
        return
    for feature_group in sorted(cross_asset_features):
        features = cross_asset_features.get(feature_group)
        if not isinstance(features, list):
            continue
        for idx, feature in enumerate(features):
            if not isinstance(feature, dict):
                continue
            prefix = f"market_state.cross_asset_features.{feature_group}[{idx}]"
            _append_unknown_ids(
                errors,
                prefix,
                "variable_ids",
                feature.get("variable_ids"),
                allowed_variable_ids,
                "not in ontology_state.allowed_variables",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "formula_ids",
                feature.get("formula_ids"),
                ontology_formula_ids,
                "not in ontology_state.allowed_formulas",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "formula_ids",
                feature.get("formula_ids"),
                knowledge_formula_ids,
                "not in knowledge_state.formulas_available",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "source_ids",
                feature.get("source_ids"),
                approved_source_ids,
                "not in knowledge_state.approved_sources_retrieved",
            )
            _append_unknown_ids(
                errors,
                prefix,
                "source_ids",
                feature.get("source_ids"),
                referenced_source_ids,
                "not referenced by ontology/formula/source refs",
            )


def _append_unknown_ids(
    errors: List[str],
    prefix: str,
    field: str,
    values: Any,
    allowed: set[str],
    reason: str,
) -> None:
    if not isinstance(values, list):
        return
    missing = sorted(value for value in values if isinstance(value, str) and value not in allowed)
    if missing:
        errors.append(f"{prefix}.{field}: {reason}: {', '.join(missing)}")


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
