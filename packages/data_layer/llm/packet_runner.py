"""Packet-strict LLM runner — validated JSON in/out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_layer.llm import openai_compatible_client as llm_client
from data_layer.llm.prompts import (
    build_aar_system_prompt,
    build_aar_user_envelope,
    build_symbolic_narrative,
)
from data_layer.openfoundry_bridge import assert_connector_valid, validate_connector
from data_layer.packet.validate import (
    validate_aar_packet_in,
    validate_aar_packet_out,
    validate_pipeline_hypothesis_response,
    validate_pipeline_request,
    validate_pipeline_response,
)

_AAR_OUTPUT_SCHEMA = Path(__file__).resolve().parents[1] / "packet" / "schema_aar_response_v1.json"
_PIPELINE_OUTPUT_SCHEMA = (
    Path(__file__).resolve().parents[1] / "packet" / "schema_pipeline_response_v1.json"
)
_HYPOTHESIS_OUTPUT_SCHEMA = (
    Path(__file__).resolve().parents[1] / "packet" / "schema_pipeline_hypothesis_response_v1.json"
)
DEFAULT_AAR_MODEL = llm_client.DEFAULT_AAR_MODEL
DEFAULT_PIPELINE_MODEL = llm_client.DEFAULT_PIPELINE_MODEL
DEFAULT_MODEL_DEVELOPMENT_MODEL = llm_client.DEFAULT_MODEL_DEVELOPMENT_MODEL


def _load_schema_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _base_aar_response(
    packet_in: Dict[str, Any],
    symbolic: Dict[str, Any],
    *,
    llm_status: str,
    llm_model: Optional[str] = None,
    llm_elapsed_s: Optional[float] = None,
    narrative_md: str = "",
    kg_annotations: Optional[List[Dict[str, Any]]] = None,
    promote_recommend: bool = False,
    llm_error: Optional[str] = None,
) -> Dict[str, Any]:
    lat = packet_in.get("latency_authority") or {}
    promote_packet = bool(lat.get("promote_candidate"))
    out: Dict[str, Any] = {
        "schema_version": "1",
        "run_id": str(packet_in.get("run_id", "")),
        "input_schema_version": str(packet_in.get("schema_version", "1")),
        "llm_model": llm_model,
        "llm_elapsed_s": llm_elapsed_s,
        "llm_status": llm_status,
        "symbolic_passed": bool(symbolic.get("passed")),
        "decision": {
            "promote_candidate_recommendation": promote_recommend and not promote_packet,
        },
        "kg_annotations": kg_annotations or [],
        "narrative_md": narrative_md,
    }
    if llm_error:
        out["llm_error"] = llm_error
    if not symbolic.get("passed"):
        out["decision"]["promote_candidate_recommendation"] = False
    return out


def _clamp_promote_recommendation(parsed: Dict[str, Any], packet_in: Dict[str, Any], symbolic: Dict[str, Any]) -> None:
    """Never allow LLM to recommend promote when symbolic or latency gates fail (BLUEPRINT §4)."""
    decision = parsed.setdefault("decision", {})
    if not _promote_allowed(packet_in, symbolic):
        decision["promote_candidate_recommendation"] = False


def _promote_allowed(packet_in: Dict[str, Any], symbolic: Dict[str, Any]) -> bool:
    if not symbolic.get("passed"):
        return False
    lat = packet_in.get("latency_authority") or {}
    if lat.get("promote_candidate") is not True:
        return False
    if lat.get("lane_pass") is False:
        return False
    if lat.get("robustness_passed") is False:
        return False
    wfc = str(lat.get("wfc_status") or "").lower()
    if wfc in ("fail", "failed", "blocked"):
        return False
    return True


def _ensure_aar_response(
    packet_in: Dict[str, Any],
    symbolic: Dict[str, Any],
    raw: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    errors = validate_aar_packet_out(raw)
    if not errors:
        return raw
    reject = _base_aar_response(
        packet_in,
        symbolic,
        llm_status="schema_reject",
        narrative_md="After-action response failed schema validation.",
        llm_error="; ".join(errors[:5]),
        **kwargs,
    )
    final_errors = validate_aar_packet_out(reject)
    if final_errors:
        raise ValueError(f"AAR skip/reject envelope invalid: {final_errors}")
    return reject


def _connector_gate(repo_root: Path) -> Optional[str]:
    try:
        assert_connector_valid(validate_connector(repo_root))
    except (ValueError, FileNotFoundError, OSError) as exc:
        return str(exc)
    return None


def _validated_pipeline_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    errors = validate_pipeline_response(resp)
    if errors:
        raise ValueError(f"PipelineResponsePacket schema errors: {errors}")
    return resp


def _pipeline_error_response(
    request: Dict[str, Any],
    *,
    llm_status: str,
    llm_model: Optional[str] = None,
    llm_error: Optional[str] = None,
    generated_at: str = "",
) -> Dict[str, Any]:
    resp: Dict[str, Any] = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "run_id": request["request_id"],
        "event_id": request["event_id"],
        "llm_model": llm_model,
        "llm_status": llm_status,
        "parsed": {
            "primary_model_id": "",
            "instrument_universe": [],
            "indicators": [],
            "source": "unavailable",
        },
        "candidates_tested": 0,
        "results": [],
        "generated_at": generated_at,
    }
    if request.get("openfoundry_meta"):
        resp["openfoundry_meta"] = request["openfoundry_meta"]
    if llm_error:
        resp["llm_error"] = llm_error
    return _validated_pipeline_response(resp)


def run_llm_on_aar_packet(
    packet_in: Dict[str, Any],
    symbolic: Dict[str, Any],
    *,
    repo_root: Path,
    model: str | None = None,
    similar_prior_runs: List[Dict[str, Any]] | None = None,
    skip_llm: bool = False,
    skip_reasons: List[str] | None = None,
) -> Dict[str, Any]:
    """Return validated AARPacketOut (or skip/unavailable envelope)."""
    model = model or DEFAULT_AAR_MODEL
    skip_reasons = skip_reasons or []

    in_errors = validate_aar_packet_in(packet_in)
    if in_errors:
        raise ValueError(f"MicrostructureAARPacket schema errors: {in_errors}")

    if skip_llm:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_flag",
                narrative_md="After-action LLM skipped by caller flag.",
            ),
        )

    if "HISTORY_GATE" in skip_reasons:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_history_gate",
                narrative_md="After-action LLM skipped: HISTORY_GATE.",
            ),
        )

    if "AUDIT_INCOMPLETE" in skip_reasons:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_audit_incomplete",
                narrative_md="After-action LLM skipped: per_trade_audit incomplete.",
            )
        )

    if not packet_in.get("pdf_citations_complete"):
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_pdf",
                narrative_md="After-action LLM skipped: PDF citations incomplete.",
            )
        )

    conn_err = _connector_gate(repo_root)
    if conn_err:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_connector",
                narrative_md=f"After-action LLM skipped: OpenFoundry connector invalid ({conn_err}).",
            ),
        )

    if not symbolic.get("passed"):
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="skipped_symbolic",
                narrative_md=build_symbolic_narrative(symbolic, packet_in),
            )
        )

    if not llm_client.llm_available():
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="unavailable",
                narrative_md="After-action LLM unavailable (OpenAI-compatible GPT-5.5 endpoint not configured).",
                llm_error="HFT3_LLM_API_KEY or OPENAI_API_KEY is not set",
            )
        )

    schema_text = _load_schema_text(_AAR_OUTPUT_SCHEMA)
    system = build_aar_system_prompt(schema_text)
    user = build_aar_user_envelope(packet_in, symbolic, similar_prior_runs=similar_prior_runs)

    result = llm_client.generate(system, user, model=model, format_json=True, num_predict=4096)
    if result.error or not result.text:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="unavailable",
                llm_model=result.model,
                llm_elapsed_s=result.elapsed_s,
                narrative_md="After-action LLM call failed.",
                llm_error=result.error or "empty response",
            )
        )

    parsed, parse_err = _parse_json_object(result.text)
    if parse_err:
        repair_user = (
            user
            + "\n\nYour prior response was invalid JSON. Return ONLY valid JSON matching the schema."
        )
        result2 = llm_client.generate(system, repair_user, model=model, format_json=True, num_predict=4096)
        if result2.text:
            parsed, parse_err = _parse_json_object(result2.text)
            result = result2

    if parse_err or not parsed:
        return _ensure_aar_response(
            packet_in,
            symbolic,
            _base_aar_response(
                packet_in,
                symbolic,
                llm_status="schema_reject",
                llm_model=result.model,
                llm_elapsed_s=result.elapsed_s,
                narrative_md="After-action LLM returned non-JSON output.",
                llm_error=parse_err or "invalid JSON",
            )
        )

    parsed.setdefault("schema_version", "1")
    parsed.setdefault("run_id", packet_in.get("run_id"))
    parsed.setdefault("input_schema_version", packet_in.get("schema_version", "1"))
    parsed["llm_model"] = result.model
    parsed["llm_elapsed_s"] = result.elapsed_s
    parsed["llm_status"] = "ok"
    parsed["symbolic_passed"] = bool(symbolic.get("passed"))
    _clamp_promote_recommendation(parsed, packet_in, symbolic)

    return _ensure_aar_response(
        packet_in,
        symbolic,
        parsed,
        llm_model=result.model,
        llm_elapsed_s=result.elapsed_s,
    )


def _hypothesis_error_response(
    request: Dict[str, Any],
    *,
    llm_status: str,
    llm_model: Optional[str] = None,
    llm_error: Optional[str] = None,
) -> Dict[str, Any]:
    resp: Dict[str, Any] = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "llm_model": llm_model,
        "llm_status": llm_status,
        "primary_model_id": "",
        "instrument_universe": [],
        "entry_rules": [],
        "exit_rules": [],
        "indicators": [],
        "feature_list": [],
        "param_ranges": {},
    }
    if llm_error:
        resp["llm_error"] = llm_error
    errors = validate_pipeline_hypothesis_response(resp)
    if errors:
        raise ValueError(f"PipelineHypothesisResponse schema errors: {errors}")
    return resp


def run_llm_on_hypothesis_request(
    request: Dict[str, Any],
    thesis: str,
    *,
    allowed_model_ids: List[str],
    model: str | None = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Packet-strict GPT-5.5 hypothesis parse (schema_pipeline_hypothesis_response_v1)."""
    model = model or DEFAULT_MODEL_DEVELOPMENT_MODEL
    req_errors = validate_pipeline_request(request)
    if req_errors:
        raise ValueError(f"PipelineRequestPacket schema errors: {req_errors}")

    if repo_root is not None:
        conn_err = _connector_gate(repo_root)
        if conn_err:
            return _hypothesis_error_response(
                request, llm_status="skipped_connector", llm_error=conn_err
            )

    schema_text = _load_schema_text(_HYPOTHESIS_OUTPUT_SCHEMA)
    system = (
        "Convert the thesis into JSON matching PipelineHypothesisResponse schema.\n"
        f"primary_model_id must be one of: {', '.join(allowed_model_ids[:44])}\n\n"
        f"Output JSON Schema:\n{schema_text}"
    )
    user = json.dumps({"pipeline_request": request, "thesis": thesis}, indent=2)

    if not llm_client.llm_available():
        return _hypothesis_error_response(
            request,
            llm_status="unavailable",
            llm_error="HFT3_LLM_API_KEY or OPENAI_API_KEY is not set",
        )

    result = llm_client.generate(system, user, model=model, format_json=True, num_predict=4096)
    if result.error or not result.text:
        return _hypothesis_error_response(
            request,
            llm_status="unavailable",
            llm_model=result.model,
            llm_error=result.error or "empty response",
        )

    parsed, parse_err = _parse_json_object(result.text)
    if parse_err or not parsed:
        return _hypothesis_error_response(
            request,
            llm_status="schema_reject",
            llm_model=result.model,
            llm_error=parse_err or "invalid JSON",
        )

    parsed.setdefault("schema_version", "1")
    parsed.setdefault("request_id", request["request_id"])
    parsed["llm_model"] = result.model
    parsed["llm_status"] = "ok"

    out_errors = validate_pipeline_hypothesis_response(parsed)
    if out_errors:
        return _hypothesis_error_response(
            request,
            llm_status="schema_reject",
            llm_model=result.model,
            llm_error="; ".join(out_errors[:5]),
        )
    slug = str(parsed.get("primary_model_id", ""))
    if slug not in set(allowed_model_ids):
        return _hypothesis_error_response(
            request,
            llm_status="schema_reject",
            llm_model=result.model,
            llm_error=f"primary_model_id not in allowed slugs: {slug!r}",
        )
    return parsed


def run_llm_on_pipeline_request(
    request: Dict[str, Any],
    *,
    model: str | None = None,
) -> Dict[str, Any]:
    """Return validated PipelineResponsePacket or error envelope."""
    model = model or DEFAULT_PIPELINE_MODEL
    req_errors = validate_pipeline_request(request)
    if req_errors:
        raise ValueError(f"PipelineRequestPacket schema errors: {req_errors}")

    schema_text = _load_schema_text(_PIPELINE_OUTPUT_SCHEMA)
    system = (
        "You are a quantitative research pipeline assistant. "
        "Respond with JSON only matching PipelineResponsePacket schema.\n\n"
        f"Output JSON Schema:\n{schema_text}"
    )
    user = json.dumps({"pipeline_request": request}, indent=2)

    if not llm_client.llm_available():
        return _pipeline_error_response(
            request,
            llm_status="unavailable",
            llm_error="HFT3_LLM_API_KEY or OPENAI_API_KEY is not set",
        )

    result = llm_client.generate(system, user, model=model, format_json=True, num_predict=4096)
    if result.error or not result.text:
        return _pipeline_error_response(
            request,
            llm_status="unavailable",
            llm_model=result.model,
            llm_error=result.error or "empty response",
        )

    parsed, parse_err = _parse_json_object(result.text)
    if parse_err or not parsed:
        return _pipeline_error_response(
            request,
            llm_status="schema_reject",
            llm_model=result.model,
            llm_error=parse_err or "invalid JSON",
        )

    parsed.setdefault("schema_version", "1")
    parsed.setdefault("request_id", request["request_id"])
    parsed.setdefault("event_id", request["event_id"])
    parsed["llm_model"] = result.model
    parsed["llm_status"] = "ok"

    resp_errors = validate_pipeline_response(parsed)
    if resp_errors:
        return _pipeline_error_response(
            request,
            llm_status="schema_reject",
            llm_model=result.model,
            llm_error="; ".join(resp_errors[:5]),
            generated_at=str(parsed.get("generated_at", "")),
        )

    return parsed


def _parse_json_object(text: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n") if "\n" in stripped else len(stripped)
        last_fence = stripped.rfind("```")
        if last_fence > first_newline:
            stripped = stripped[first_newline + 1 : last_fence].strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "response is not a JSON object"
    return data, None
