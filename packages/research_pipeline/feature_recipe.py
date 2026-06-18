"""Feature-recipe contract for feature-family research (Phase 1).

Extends candidates with typed per-family metadata, PIT fields, and a deterministic
feature_recipe_hash shared by VectorBT and HftBacktest handoff.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Mapping, MutableMapping, Sequence

from backtest_pipeline.src.feature_plane import FEATURE_FAMILIES, MODEL_CONSUMPTION_VALUES

FEATURE_RECIPE_SCHEMA_VERSION = "feature_recipe.v1"
FEATURE_VERSION_FS_V1 = "fs_v1"

MISSINGNESS_STATES = frozenset(
    {
        "present",
        "missing",
        "stale",
        "embargoed",
        "malformed",
        "proxy_only",
        "available_not_selected",
    }
)

PIT_PROOF_STATES = frozenset({"pending", "declared", "proven", "not_applicable", "rejected"})

_ISO_TS = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO_TS.match(text):
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class FamilyMetadata:
    family_id: str
    source_ids: list[str] = field(default_factory=list)
    source_timestamp: str | None = None
    feature_availability_timestamp: str | None = None
    target_decision_timestamp: str | None = None
    units: str = "dimensionless"
    feature_version: str = FEATURE_VERSION_FS_V1
    missingness_state: str = "missing"
    staleness_state: str = "not_measured"
    pit_proof: str = "pending"
    model_consumption_state: str = "not_used"
    why_not_used_or_sidelined: str | None = None
    selected_features: list[str] = field(default_factory=list)
    source_symbols: list[str] = field(default_factory=list)
    lag_windows: list[int] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    allowed_context_events: list[str] = field(default_factory=list)
    lookback_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.family_id not in FEATURE_FAMILIES:
            errors.append(f"unknown_family:{self.family_id}")
        if self.model_consumption_state not in MODEL_CONSUMPTION_VALUES:
            errors.append(f"invalid_consumption:{self.family_id}")
        if self.missingness_state not in MISSINGNESS_STATES:
            errors.append(f"invalid_missingness:{self.family_id}")
        if self.pit_proof not in PIT_PROOF_STATES:
            errors.append(f"invalid_pit_proof:{self.family_id}")
        src = _parse_ts(self.source_timestamp)
        avail = _parse_ts(self.feature_availability_timestamp)
        target = _parse_ts(self.target_decision_timestamp)
        if src and target and src > target:
            errors.append(f"future_source_timestamp:{self.family_id}")
        if avail and target and avail > target:
            errors.append(f"future_availability_timestamp:{self.family_id}")
        if self.model_consumption_state == "consumed" and self.missingness_state in {
            "missing",
            "malformed",
            "proxy_only",
        }:
            errors.append(f"consumed_with_bad_missingness:{self.family_id}")
        return errors


@dataclass
class FeatureRecipe:
    schema_version: str = FEATURE_RECIPE_SCHEMA_VERSION
    model_id: str = ""
    target_symbol: str = "MES"
    research_clock: str = "scheduled_event"
    target_event_id: str | None = None
    feature_families: dict[str, dict[str, Any]] = field(default_factory=dict)
    interactions: list[dict[str, Any]] = field(default_factory=list)
    context_gates: list[dict[str, Any]] = field(default_factory=list)
    regime_gates: list[dict[str, Any]] = field(default_factory=list)
    target_horizon: str | None = None
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    feature_recipe_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("feature_recipe_hash") is None:
            payload.pop("feature_recipe_hash", None)
        return payload

    def validate(self) -> list[str]:
        errors: list[str] = []
        for family in FEATURE_FAMILIES:
            if family not in self.feature_families:
                errors.append(f"missing_family:{family}")
        for family, row in self.feature_families.items():
            if not isinstance(row, Mapping):
                errors.append(f"family_not_mapping:{family}")
                continue
            valid_keys = {f.name for f in fields(FamilyMetadata)} - {"family_id"}
            meta = FamilyMetadata(
                family_id=family,
                **{k: v for k, v in row.items() if k in valid_keys},
            )
            errors.extend(meta.validate())
        return errors


def _default_family_row(
    *,
    family_id: str,
    model_consumption: str,
    why: str,
    missingness: str = "missing",
    selected_features: Sequence[str] | None = None,
    source_symbols: Sequence[str] | None = None,
    pit_proof: str = "pending",
) -> dict[str, Any]:
    return FamilyMetadata(
        family_id=family_id,
        source_ids=[],
        missingness_state=missingness,
        pit_proof=pit_proof,
        model_consumption_state=model_consumption,
        why_not_used_or_sidelined=why,
        selected_features=list(selected_features or []),
        source_symbols=list(source_symbols or []),
    ).to_dict()


def default_feature_families(
    *,
    model_id: str,
    feature_list: Sequence[str],
    target_symbol: str,
    research_clock: str = "scheduled_event",
) -> dict[str, dict[str, Any]]:
    """Honest Phase-1 defaults: primary catalog-eligible; others sidelined until wired."""
    primary_features = [f for f in feature_list if f] or [model_id]
    clock = research_clock.strip().lower()
    continuous_scope = clock == "continuous_intraday"

    families: dict[str, dict[str, Any]] = {
        "primary_fs_v1": _default_family_row(
            family_id="primary_fs_v1",
            model_consumption="not_measured",
            why="vectorbt_screening_has_not_observed_fs_v1_row_loop_consumption",
            missingness="available_not_selected",
            selected_features=primary_features,
            pit_proof="pending",
        ),
        "cross_asset_futures": _default_family_row(
            family_id="cross_asset_futures",
            model_consumption="sidelined_missing_data",
            why="multi_symbol_leader_mbo_not_wired_for_this_candidate",
            source_symbols=["ES", "NQ", "ZN"],
        ),
        "vix_vvix_sensor": _default_family_row(
            family_id="vix_vvix_sensor",
            model_consumption="not_used",
            why="vix_sensor_not_selected_for_this_recipe",
        ),
        "vix_options": _default_family_row(
            family_id="vix_options",
            model_consumption="not_used",
            why="vix_options_not_selected_for_this_recipe",
        ),
        "cme_options_context": _default_family_row(
            family_id="cme_options_context",
            model_consumption="sidelined_scope",
            why="cme_options_context_data_requirements_not_satisfied",
        ),
        "macro_context": _default_family_row(
            family_id="macro_context",
            model_consumption="not_used",
            why="macro_context_uplift_not_declared_on_this_recipe",
        ),
        "continuous_session": _default_family_row(
            family_id="continuous_session",
            model_consumption="not_measured" if continuous_scope else "sidelined_scope",
            why=(
                "continuous_intraday_clock_declared_consumption_not_observed"
                if continuous_scope
                else "continuous_intraday_clock_out_of_scope_for_scheduled_screen"
            ),
            missingness="available_not_selected" if continuous_scope else "missing",
        ),
        "latency_state": _default_family_row(
            family_id="latency_state",
            model_consumption="not_used",
            why="latency_state_not_selected_for_this_recipe",
        ),
    }
    return families


def compute_feature_recipe_hash(recipe: Mapping[str, Any]) -> str:
    """Deterministic hash excluding identity and freeze timestamps."""
    excluded = {
        "feature_recipe_hash",
        "frozen_at_utc",
        "candidate_id",
        "manifest_id",
        "generation_index",
        "parent_candidate_id",
        "proposal_reason",
    }
    payload = {k: v for k, v in recipe.items() if k not in excluded}
    families = payload.get("feature_families")
    if isinstance(families, Mapping):
        payload = dict(payload)
        payload["feature_families"] = {
            k: families[k] for k in sorted(families.keys(), key=str)
        }
    return _hash_payload(payload)


def build_feature_recipe(
    *,
    model_id: str,
    strategy_params: Mapping[str, Any],
    feature_list: Sequence[str],
    target_symbol: str = "MES",
    research_clock: str = "scheduled_event",
    target_event_id: str | None = None,
    feature_families: Mapping[str, Mapping[str, Any]] | None = None,
    interactions: Sequence[Mapping[str, Any]] | None = None,
    context_gates: Sequence[Mapping[str, Any]] | None = None,
    regime_gates: Sequence[Mapping[str, Any]] | None = None,
    target_horizon: str | None = None,
) -> FeatureRecipe:
    families = (
        {k: dict(v) for k, v in feature_families.items()}
        if feature_families
        else default_feature_families(
            model_id=model_id,
            feature_list=feature_list,
            target_symbol=target_symbol,
            research_clock=research_clock,
        )
    )
    recipe = FeatureRecipe(
        model_id=model_id,
        target_symbol=target_symbol,
        research_clock=research_clock,
        target_event_id=target_event_id,
        feature_families=families,
        interactions=[dict(x) for x in (interactions or [])],
        context_gates=[dict(x) for x in (context_gates or [])],
        regime_gates=[dict(x) for x in (regime_gates or [])],
        target_horizon=target_horizon,
        execution_assumptions=dict(strategy_params),
    )
    recipe_dict = recipe.to_dict()
    recipe.feature_recipe_hash = compute_feature_recipe_hash(recipe_dict)
    return recipe


def attach_feature_recipe_to_candidate(
    candidate: Any,
    *,
    parsed: Any | None = None,
    target_event_id: str | None = None,
    target_symbol: str = "MES",
    research_clock: str = "scheduled_event",
) -> Any:
    """Return a new CandidateModel with feature_recipe fields populated."""
    from research_pipeline.types import CandidateModel

    feature_list = list(getattr(parsed, "feature_list", None) or [])
    if not feature_list and parsed is not None:
        feature_list = [getattr(parsed, "primary_model_id", candidate.model_id)]

    existing = getattr(candidate, "feature_recipe", None)
    if isinstance(existing, Mapping) and existing.get("feature_families"):
        recipe = FeatureRecipe(
            schema_version=str(existing.get("schema_version") or FEATURE_RECIPE_SCHEMA_VERSION),
            model_id=str(existing.get("model_id") or candidate.model_id),
            target_symbol=str(existing.get("target_symbol") or target_symbol),
            research_clock=str(existing.get("research_clock") or research_clock),
            target_event_id=existing.get("target_event_id") or target_event_id,
            feature_families=dict(existing.get("feature_families") or {}),
            interactions=list(existing.get("interactions") or []),
            context_gates=list(existing.get("context_gates") or []),
            regime_gates=list(existing.get("regime_gates") or []),
            target_horizon=existing.get("target_horizon"),
            execution_assumptions=dict(candidate.strategy_params),
        )
        recipe_dict = recipe.to_dict()
        recipe.feature_recipe_hash = compute_feature_recipe_hash(recipe_dict)
    else:
        recipe = build_feature_recipe(
            model_id=candidate.model_id,
            strategy_params=candidate.strategy_params,
            feature_list=feature_list,
            target_symbol=target_symbol,
            research_clock=research_clock,
            target_event_id=target_event_id,
        )

    meta = dict(candidate.metadata or {})
    meta["feature_recipe_hash"] = recipe.feature_recipe_hash
    meta["research_clock"] = recipe.research_clock
    meta["symbol"] = recipe.target_symbol
    if target_event_id:
        meta["target_event_id"] = target_event_id

    return CandidateModel(
        candidate_id=candidate.candidate_id,
        model_id=candidate.model_id,
        strategy_params=dict(candidate.strategy_params),
        thesis=candidate.thesis,
        metadata=meta,
        feature_recipe=recipe.to_dict(),
        feature_recipe_hash=recipe.feature_recipe_hash,
        target_symbol=recipe.target_symbol,
        research_clock=recipe.research_clock,
        target_event_id=recipe.target_event_id,
    )


def candidate_identity_hash(candidate: Any) -> str:
    """Dedup key: prefer feature_recipe_hash when present."""
    recipe_hash = getattr(candidate, "feature_recipe_hash", None) or (
        (candidate.metadata or {}).get("feature_recipe_hash")
    )
    if recipe_hash:
        return str(recipe_hash)
    from workbench.src.core.params import param_hash_from_dict

    return param_hash_from_dict(candidate.model_id, candidate.strategy_params)


def validate_recipe_pit_timestamps(recipe: Mapping[str, Any]) -> list[str]:
    """Reject future source timestamps relative to target decision time."""
    fr = FeatureRecipe(
        model_id=str(recipe.get("model_id") or ""),
        target_symbol=str(recipe.get("target_symbol") or "MES"),
        research_clock=str(recipe.get("research_clock") or "scheduled_event"),
        target_event_id=recipe.get("target_event_id"),
        feature_families=dict(recipe.get("feature_families") or {}),
        execution_assumptions=dict(recipe.get("execution_assumptions") or {}),
    )
    return fr.validate()
