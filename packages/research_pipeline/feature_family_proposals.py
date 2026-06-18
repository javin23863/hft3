"""Bounded feature-family recipe variants for autoresearch Gen N+1."""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping, Sequence

from research_pipeline.feature_recipe import (
    attach_feature_recipe_to_candidate,
    compute_feature_recipe_hash,
    validate_recipe_pit_timestamps,
)
from research_pipeline.types import CandidateModel, ParsedHypothesis

FAMILY_VARIANT_IDS: tuple[str, ...] = (
    "cross_asset_es_leader",
    "vix_sensor_declared",
    "macro_context_uplift",
    "latency_state_declared",
    "continuous_intraday_clock",
)


def list_family_variant_ids() -> tuple[str, ...]:
    return FAMILY_VARIANT_IDS


def apply_family_variant_to_recipe(
    recipe: Mapping[str, Any],
    *,
    variant_id: str,
    target_event_id: str | None = None,
) -> dict[str, Any]:
    """Return a deep copy of *recipe* with an honest family-uplift mutation."""
    if variant_id not in FAMILY_VARIANT_IDS:
        raise ValueError(f"unknown_family_variant:{variant_id}")

    out = copy.deepcopy(dict(recipe))
    families = dict(out.get("feature_families") or {})
    if not families:
        raise ValueError("recipe_missing_feature_families")

    if variant_id == "cross_asset_es_leader":
        fam = dict(families.get("cross_asset_futures") or {})
        fam.update(
            {
                "model_consumption_state": "not_measured",
                "missingness_state": "missing",
                "selected_features": ["es_leader_momentum", "nq_co_move"],
                "source_symbols": ["ES", "NQ", "ZN"],
                "why_not_used_or_sidelined": "cross_asset_leader_declared_for_family_search",
                "pit_proof": "pending",
            }
        )
        families["cross_asset_futures"] = fam

    elif variant_id == "vix_sensor_declared":
        fam = dict(families.get("vix_vvix_sensor") or {})
        fam.update(
            {
                "model_consumption_state": "not_measured",
                "missingness_state": "missing",
                "selected_features": ["vix_level", "vvix_level"],
                "why_not_used_or_sidelined": "vix_sensor_uplift_declared_for_family_search",
                "pit_proof": "pending",
            }
        )
        families["vix_vvix_sensor"] = fam

    elif variant_id == "macro_context_uplift":
        events = [target_event_id] if target_event_id else []
        fam = dict(families.get("macro_context") or {})
        fam.update(
            {
                "model_consumption_state": "not_measured",
                "missingness_state": "missing",
                "allowed_context_events": events,
                "selected_features": ["macro_surprise_z"],
                "why_not_used_or_sidelined": "macro_context_uplift_declared_for_family_search",
                "pit_proof": "pending",
            }
        )
        families["macro_context"] = fam

    elif variant_id == "latency_state_declared":
        fam = dict(families.get("latency_state") or {})
        fam.update(
            {
                "model_consumption_state": "not_measured",
                "missingness_state": "missing",
                "selected_features": ["order_latency_ms", "feed_latency_ms"],
                "why_not_used_or_sidelined": "latency_state_uplift_declared_for_family_search",
                "pit_proof": "pending",
            }
        )
        families["latency_state"] = fam

    elif variant_id == "continuous_intraday_clock":
        out["research_clock"] = "continuous_intraday"
        fam = dict(families.get("continuous_session") or {})
        fam.update(
            {
                "model_consumption_state": "not_measured",
                "missingness_state": "missing",
                "why_not_used_or_sidelined": (
                    "continuous_intraday_clock_declared_for_family_search;"
                    "scheduled_event_scope_retained_until_continuous_pit_wired"
                ),
                "pit_proof": "pending",
            }
        )
        families["continuous_session"] = fam

    out["feature_families"] = families
    out.pop("feature_recipe_hash", None)
    out["feature_recipe_hash"] = compute_feature_recipe_hash(out)
    pit_errors = validate_recipe_pit_timestamps(out)
    if pit_errors:
        raise ValueError(f"family_variant_pit_invalid:{variant_id}:{','.join(pit_errors)}")
    return out


def _elite_base_recipe(
    elite: Mapping[str, Any],
    *,
    parsed: ParsedHypothesis,
    target_event_id: str | None,
    target_symbol: str,
    research_clock: str,
) -> dict[str, Any]:
    existing = elite.get("feature_recipe")
    if isinstance(existing, Mapping) and existing.get("feature_families"):
        return copy.deepcopy(dict(existing))

    metrics = elite.get("metrics") if isinstance(elite.get("metrics"), Mapping) else {}
    from_metrics = metrics.get("feature_recipe") if isinstance(metrics, Mapping) else None
    if isinstance(from_metrics, Mapping) and from_metrics.get("feature_families"):
        return copy.deepcopy(dict(from_metrics))

    model_id = str(elite.get("model_id") or parsed.primary_model_id)
    params = dict(elite.get("strategy_params") or {})
    bootstrap = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id=str(elite.get("candidate_id") or "elite"),
            model_id=model_id,
            strategy_params=params,
            thesis=parsed.thesis,
        ),
        parsed=parsed,
        target_event_id=target_event_id,
        target_symbol=target_symbol,
        research_clock=research_clock,
    )
    return copy.deepcopy(dict(bootstrap.feature_recipe or {}))


def _variant_candidate_id(*, parent_id: str, variant_id: str, recipe_hash: str) -> str:
    digest = hashlib.sha256(f"{parent_id}:{variant_id}:{recipe_hash}".encode()).hexdigest()
    return f"fv_{digest[:16]}"


def propose_family_variant_candidates(
    *,
    elites: Sequence[Mapping[str, Any]],
    parsed: ParsedHypothesis,
    tested_hashes: set[str],
    max_candidates: int,
    target_event_id: str | None = None,
    target_symbol: str = "MES",
    research_clock: str = "scheduled_event",
) -> list[CandidateModel]:
    """Emit bounded family-recipe variants from validated elites."""
    out: list[CandidateModel] = []
    local_seen: set[str] = set()
    if max_candidates <= 0:
        return out

    for elite in elites:
        model_id = str(elite.get("model_id") or parsed.primary_model_id)
        params = dict(elite.get("strategy_params") or {})
        parent_id = str(elite.get("candidate_id") or "elite")
        try:
            base_recipe = _elite_base_recipe(
                elite,
                parsed=parsed,
                target_event_id=target_event_id,
                target_symbol=target_symbol,
                research_clock=research_clock,
            )
        except (ValueError, TypeError):
            continue

        for variant_id in FAMILY_VARIANT_IDS:
            if len(out) >= max_candidates:
                return out
            try:
                variant_recipe = apply_family_variant_to_recipe(
                    base_recipe,
                    variant_id=variant_id,
                    target_event_id=target_event_id,
                )
            except ValueError:
                continue

            recipe_hash = str(variant_recipe.get("feature_recipe_hash") or "")
            if (
                not recipe_hash
                or recipe_hash in tested_hashes
                or recipe_hash in local_seen
            ):
                continue

            clock = str(variant_recipe.get("research_clock") or research_clock)
            cand = CandidateModel(
                candidate_id=_variant_candidate_id(
                    parent_id=parent_id,
                    variant_id=variant_id,
                    recipe_hash=recipe_hash,
                ),
                model_id=model_id,
                strategy_params=params,
                thesis=parsed.thesis,
                metadata={
                    "source_model": parsed.primary_model_id,
                    "strategy_family": model_id,
                    "elite_parent": parent_id,
                    "refinement": "family_variant",
                    "family_variant_id": variant_id,
                    "proposal_reason": f"family_variant:{variant_id}",
                },
                feature_recipe=variant_recipe,
                feature_recipe_hash=recipe_hash,
                target_symbol=str(variant_recipe.get("target_symbol") or target_symbol),
                research_clock=clock,
                target_event_id=variant_recipe.get("target_event_id") or target_event_id,
            )
            local_seen.add(recipe_hash)
            out.append(cand)

    return out
