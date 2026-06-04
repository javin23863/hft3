"""Machine-efficient pre-run idea generation for the research pipeline."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from features_engine.src.model_registry import all_slugs, load_model_registry

from data_layer.llm.packet_runner import run_llm_on_idea_generation_request
from data_layer.packet.validate import validate_pipeline_idea_set
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.model_generation import generate_candidates
from research_pipeline.review_memory import (
    build_review_memory,
    memory_refs,
    schema_memory_items,
)
from research_pipeline.types import CandidateModel, EvaluationResult, ParsedHypothesis

ALLOWED_LANE_CODES = ["cme", "crypto", "options", "equities"]
IDEA_STATUSES = {
    "proposed",
    "schema_reject",
    "static_reject",
    "queued_for_test",
    "tested_fail",
    "tested_pass",
}


def hypothesis_model_ids() -> List[str]:
    models = load_model_registry().get("models", {})
    return sorted(k for k, v in models.items() if v.get("kind") == "hypothesis")


def _base_refs(request: Dict[str, Any], memory: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    refs: Dict[str, Dict[str, str]] = {
        "ref_event": {"type": "event", "value": str(request.get("event_id") or "")},
        "ref_request": {"type": "source", "value": str(request.get("request_id") or "")},
    }
    refs.update(memory_refs(memory))
    return refs


def _rank_inputs() -> Dict[str, float]:
    return {
        "novelty": 0.0,
        "evidence_coverage": 0.0,
        "lane_fit": 1.0,
        "prior_failure_overlap": 0.0,
        "validation_readiness": 1.0,
    }


def _fallback_packet(
    request: Dict[str, Any],
    *,
    thesis: str,
    refs: Dict[str, Any],
    review_memory: List[Dict[str, Any]],
    allowed_model_ids: List[str],
    max_candidates: int,
    llm_status: str,
    llm_model: str | None = None,
    llm_error: str | None = None,
) -> Dict[str, Any]:
    parsed = parse_hypothesis(thesis, use_llm=False)
    idea = {
        "idea_id": "idea_001",
        "status": "proposed",
        "lane_code": "cme",
        "thesis_code": parsed.thesis,
        "instrument_ids": parsed.instrument_universe or ["MES"],
        "primary_model_id": parsed.primary_model_id,
        "feature_ids": parsed.feature_list or [parsed.primary_model_id],
        "param_ranges": parsed.param_ranges or {"signal_threshold": [0.05, 0.35]},
        "entry_rule_codes": parsed.entry_rules,
        "exit_rule_codes": parsed.exit_rules,
        "risk_codes": ["must_pass_existing_pipeline_gates"],
        "evidence_ref_ids": [item["memory_id"] for item in review_memory],
        "rank_inputs": _rank_inputs(),
        "rationale_tokens": ["heuristic", "fallback"],
    }
    packet: Dict[str, Any] = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "llm_model": llm_model,
        "llm_status": llm_status,
        "refs": refs,
        "constraints": {
            "allowed_model_ids": allowed_model_ids,
            "allowed_lane_codes": ALLOWED_LANE_CODES,
            "max_candidates": max_candidates,
            "no_promotion_authority": True,
        },
        "review_memory": review_memory,
        "ideas": [idea],
    }
    if llm_error:
        packet["llm_error"] = llm_error
    errors = validate_pipeline_idea_set(packet)
    if errors:
        raise ValueError(f"fallback PipelineIdeaSet schema errors: {errors}")
    return packet


def generate_idea_set(
    request: Dict[str, Any],
    *,
    thesis: str,
    repo_root: Path,
    max_ideas: int,
    max_candidates: int,
    review_memory_limit: int = 5,
    use_llm: bool = True,
    temperature: float | None = None,
    top_p: float | None = None,
) -> Dict[str, Any]:
    memory_raw = build_review_memory(
        repo_root,
        event_id=str(request.get("event_id") or ""),
        limit=review_memory_limit,
    )
    memory = schema_memory_items(memory_raw)
    refs = _base_refs(request, memory_raw)
    allowed_model_ids = hypothesis_model_ids()

    if not use_llm:
        packet = _fallback_packet(
            request,
            thesis=thesis,
            refs=refs,
            review_memory=memory,
            allowed_model_ids=allowed_model_ids,
            max_candidates=max_candidates,
            llm_status="skipped_no_llm",
        )
    else:
        packet = run_llm_on_idea_generation_request(
            request,
            thesis,
            allowed_model_ids=allowed_model_ids,
            allowed_lane_codes=ALLOWED_LANE_CODES,
            review_memory=memory,
            refs=refs,
            max_candidates=max_candidates,
            repo_root=repo_root,
            temperature=temperature,
            top_p=top_p,
        )
        if packet.get("llm_status") != "ok" or not packet.get("ideas"):
            packet = _fallback_packet(
                request,
                thesis=thesis,
                refs=refs,
                review_memory=memory,
                allowed_model_ids=allowed_model_ids,
                max_candidates=max_candidates,
                llm_status=str(packet.get("llm_status") or "unavailable"),
                llm_model=packet.get("llm_model"),
                llm_error=packet.get("llm_error"),
            )

    ideas = [idea for idea in (packet.get("ideas") or []) if isinstance(idea, dict)]
    ideas = sorted(
        ideas,
        key=lambda idea: (
            str(idea.get("lane_code") or ""),
            str(idea.get("primary_model_id") or ""),
            str(idea.get("thesis_code") or ""),
        ),
    )
    for index, idea in enumerate(ideas):
        idea["idea_id"] = f"idea_{index + 1:03d}"
    packet["ideas"] = ideas[:max_ideas]
    errors = validate_pipeline_idea_set(packet)
    if errors:
        raise ValueError(f"PipelineIdeaSet schema errors: {errors}")
    return packet


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _static_errors(
    idea: Dict[str, Any],
    *,
    constraints: Dict[str, Any],
    refs: Dict[str, Any],
    review_memory: List[Dict[str, Any]],
) -> List[str]:
    errors: List[str] = []
    allowed_models = set(constraints.get("allowed_model_ids") or [])
    allowed_lanes = set(constraints.get("allowed_lane_codes") or [])
    model_slugs = set(all_slugs())
    valid_feature_refs = model_slugs | allowed_models
    memory_ids = {str(item.get("memory_id")) for item in review_memory}
    ref_ids = set(refs) | memory_ids

    if constraints.get("no_promotion_authority") is not True:
        errors.append("constraint_no_promotion_authority_not_true")
    if idea.get("primary_model_id") not in allowed_models:
        errors.append("primary_model_id_not_allowed")
    if idea.get("lane_code") not in allowed_lanes:
        errors.append("lane_code_not_allowed")
    for feature_id in idea.get("feature_ids") or []:
        if feature_id not in valid_feature_refs:
            errors.append("feature_id_not_valid")
            break
        if feature_id in model_slugs and feature_id not in allowed_models:
            errors.append("feature_model_id_not_allowed")
            break
    param_ranges = idea.get("param_ranges") or {}
    if not param_ranges:
        errors.append("param_ranges_empty")
    for values in param_ranges.values():
        if not isinstance(values, list) or len(values) != 2:
            errors.append("param_range_bad_shape")
            continue
        lo, hi = values
        if not _is_number(lo) or not _is_number(hi) or float(hi) <= float(lo):
            errors.append("param_range_not_increasing")
    for ref_id in idea.get("evidence_ref_ids") or []:
        if ref_id not in ref_ids:
            errors.append("evidence_ref_id_unknown")
            break
    return sorted(set(errors))


def static_filter_ideas(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    constraints = packet.get("constraints") or {}
    refs = packet.get("refs") or {}
    review_memory = list(packet.get("review_memory") or [])
    queued: List[Dict[str, Any]] = []
    for idea in packet.get("ideas") or []:
        if not isinstance(idea, dict):
            continue
        errors = _static_errors(
            idea,
            constraints=constraints,
            refs=refs,
            review_memory=review_memory,
        )
        if errors:
            idea["status"] = "static_reject"
            idea["static_error_codes"] = errors
        else:
            idea["status"] = "queued_for_test"
            idea.pop("static_error_codes", None)
            queued.append(idea)
    return sorted(queued, key=lambda row: str(row.get("idea_id") or ""))


def parsed_from_idea(idea: Dict[str, Any]) -> ParsedHypothesis:
    model_id = str(idea.get("primary_model_id") or "")
    feature_ids = list(idea.get("feature_ids") or [])
    return ParsedHypothesis(
        thesis=str(idea.get("thesis_code") or ""),
        instrument_universe=list(idea.get("instrument_ids") or ["MES"]),
        entry_rules=list(idea.get("entry_rule_codes") or []),
        exit_rules=list(idea.get("exit_rule_codes") or []),
        indicators=feature_ids or [model_id],
        feature_list=[model_id],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id=model_id,
        source="idea_set",
        llm_status=str(idea.get("status") or ""),
    )


def candidates_from_ideas(
    packet: Dict[str, Any],
    *,
    max_candidates: int,
    expand_for_vectorbt: bool = False,
) -> List[CandidateModel]:
    queued = static_filter_ideas(packet)
    candidates: List[CandidateModel] = []
    seen: set[str] = set()
    generators = [
        (
            idea,
            generate_candidates(
                parsed_from_idea(idea),
                max_candidates=max_candidates,
                expand_for_vectorbt=expand_for_vectorbt,
            ),
        )
        for idea in queued
    ]
    while generators and len(candidates) < max_candidates:
        next_round = []
        for idea, generator in generators:
            try:
                candidate = next(generator)
            except StopIteration:
                continue
            if candidate.candidate_id in seen:
                next_round.append((idea, generator))
                continue
            seen.add(candidate.candidate_id)
            candidate.metadata.update(
                {
                    "idea_id": idea.get("idea_id"),
                    "idea_status": idea.get("status"),
                    "idea_lane_code": idea.get("lane_code"),
                    "idea_queue_index": len(candidates),
                }
            )
            candidates.append(candidate)
            if len(candidates) >= max_candidates:
                break
            next_round.append((idea, generator))
        generators = next_round
    return candidates


def update_idea_statuses_from_results(
    packet: Dict[str, Any],
    results: Iterable[EvaluationResult],
) -> None:
    outcome: Dict[str, bool] = {}
    for result in results:
        idea_id = result.candidate.metadata.get("idea_id")
        if not idea_id:
            continue
        outcome[str(idea_id)] = outcome.get(str(idea_id), False) or result.passes_all_gates()
    tested_ids = {
        str(result.candidate.metadata.get("idea_id"))
        for result in results
        if result.candidate.metadata.get("idea_id")
    }
    for idea in packet.get("ideas") or []:
        idea_id = str(idea.get("idea_id") or "")
        if idea.get("status") != "queued_for_test" or idea_id not in tested_ids:
            continue
        idea["status"] = "tested_pass" if outcome.get(idea_id) else "tested_fail"


def mark_queued_ideas_without_candidates_failed(
    packet: Dict[str, Any],
    surviving_idea_ids: Iterable[str],
) -> None:
    surviving = {str(idea_id) for idea_id in surviving_idea_ids if idea_id}
    for idea in packet.get("ideas") or []:
        idea_id = str(idea.get("idea_id") or "")
        if idea.get("status") == "queued_for_test" and idea_id not in surviving:
            idea["status"] = "tested_fail"


def idea_summary(packet: Dict[str, Any], *, candidates_from_ideas_count: int) -> Dict[str, int]:
    statuses = [str(idea.get("status") or "") for idea in packet.get("ideas") or []]
    return {
        "ideas_generated": len(statuses),
        "ideas_static_rejected": statuses.count("static_reject"),
        "ideas_queued_for_test": statuses.count("queued_for_test"),
        "ideas_tested_fail": statuses.count("tested_fail"),
        "ideas_tested_pass": statuses.count("tested_pass"),
        "candidates_from_ideas": candidates_from_ideas_count,
    }


def candidate_test_counts(results: Iterable[EvaluationResult]) -> Tuple[int, int]:
    tested = 0
    passed = 0
    for result in results:
        if result.candidate.metadata.get("idea_id"):
            tested += 1
            if result.passes_all_gates():
                passed += 1
    return tested, passed
