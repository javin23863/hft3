"""Feature-plane contract for VectorBT screening artifacts.

Separates catalog eligibility from model feature consumption and refuses
mislabeled full-product evidence per VECTORBT_SCREENING_ENGINE_SPEC.md §Feature-Complete Data Plane Contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, MutableMapping, Sequence

FEATURE_PLANE_STATUS_FEATURE_COMPLETE = "feature_complete_pit_declared"
FEATURE_PLANE_STATUS_SCHEDULED_EVENT_ONLY = "scheduled_event_only"
FEATURE_PLANE_STATUS_BAR_STUB = "bar_stub_research_only"
FEATURE_PLANE_STATUS_INCOMPLETE = "incomplete_feature_plane"

FEATURE_PLANE_STATUSES = frozenset(
    {
        FEATURE_PLANE_STATUS_FEATURE_COMPLETE,
        FEATURE_PLANE_STATUS_SCHEDULED_EVENT_ONLY,
        FEATURE_PLANE_STATUS_BAR_STUB,
        FEATURE_PLANE_STATUS_INCOMPLETE,
    }
)

FEATURE_FAMILIES: Sequence[str] = (
    "primary_fs_v1",
    "cross_asset_futures",
    "vix_vvix_sensor",
    "vix_options",
    "cme_options_context",
    "macro_context",
    "continuous_session",
    "latency_state",
)

FAMILY_STATUS_FIELDS = {
    "cross_asset_futures": "cross_asset_alignment_status",
    "vix_vvix_sensor": "vix_sensor_status",
    "vix_options": "vix_options_status",
    "cme_options_context": "cme_options_context_status",
    "latency_state": "latency_feature_status",
}

MODEL_CONSUMPTION_VALUES = frozenset(
    {
        "consumed",
        "not_used",
        "sidelined_missing_data",
        "sidelined_scope",
        "not_measured",
    }
)
CATALOG_ELIGIBILITY_VALUES = frozenset({"eligible", "not_eligible", "not_measured"})

FEATURE_PLANE_ARTIFACT_FIELDS = (
    "feature_plane_status",
    "feature_usage_manifest_hash",
    "feature_usage_manifest",
    "model_feature_usage_status",
    "declared_context_sets",
    "target_event_type_or_null",
    "allowed_context_set_id_or_null",
    "context_feature_coverage_status",
    "context_ablation_status",
    "continuous_clock_status",
    "cross_asset_alignment_status",
    "vix_sensor_status",
    "vix_options_status",
    "cme_options_context_status",
    "latency_feature_status",
    "data_scope_skip_manifest_hash",
    "full_product_evidence_status",
)

_MISLEADING_FULL_PRODUCT_STATUSES = frozenset(
    {"measured", "pass", "covered", "consumed", "in_scope", "complete", "pit_declared"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def compute_feature_usage_manifest_hash(manifest: Mapping[str, Any]) -> str:
    return _hash_payload(manifest)


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_bar_stub_path(*, bar_construction_id: str, feature_set_id: str, feature_set_hash: str) -> bool:
    bar_id = _text(bar_construction_id)
    fs_id = _text(feature_set_id)
    fs_hash = _text(feature_set_hash)
    if "ohlcv" in bar_id or "bar" in bar_id:
        return True
    if "pilot" in fs_id or "pilot" in fs_hash or "unknown" in fs_id:
        return True
    if fs_hash.startswith("pilot_requires"):
        return True
    return False


def _family_row(
    *,
    catalog_eligibility: str,
    model_consumption: str,
    why_not_used_or_sidelined: str | None = None,
    evidence_scope: str = "catalog_eligibility_not_model_usage",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "catalog_eligibility": catalog_eligibility,
        "model_consumption": model_consumption,
        "evidence_scope": evidence_scope,
    }
    if why_not_used_or_sidelined:
        row["why_not_used_or_sidelined"] = why_not_used_or_sidelined
    return row


def build_feature_usage_manifest(
    *,
    bar_construction_id: str,
    feature_set_id: str,
    feature_set_hash: str,
    research_clock: str,
    screening_scope: str,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build per-family manifest separating catalog eligibility from model consumption."""
    override_manifest = dict((overrides or {}).get("feature_usage_manifest") or {})
    if override_manifest:
        return dict(override_manifest)

    bar_stub = _is_bar_stub_path(
        bar_construction_id=bar_construction_id,
        feature_set_id=feature_set_id,
        feature_set_hash=feature_set_hash,
    )
    fs_catalog = "eligible" if "fs_v1" in _text(feature_set_id) else "not_measured"
    primary_consumption = "not_used" if bar_stub else "not_measured"
    primary_why = (
        "bar_ohlcv_stub_not_fs_v1_row_loop"
        if bar_stub
        else "model_feature_consumption_not_observed_in_screening_path"
    )

    manifest: dict[str, Any] = {
        "primary_fs_v1": _family_row(
            catalog_eligibility=fs_catalog,
            model_consumption=primary_consumption,
            why_not_used_or_sidelined=primary_why,
        ),
        "cross_asset_futures": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="cross_asset_alignment_not_implemented_in_vectorbt_screen",
        ),
        "vix_vvix_sensor": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="vix_sensor_injection_not_observed_in_vectorbt_screen",
        ),
        "vix_options": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="vix_options_context_not_observed_in_vectorbt_screen",
        ),
        "cme_options_context": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="cme_options_context_not_observed_in_vectorbt_screen",
        ),
        "macro_context": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="macro_context_uplift_not_measured_in_vectorbt_screen",
        ),
        "continuous_session": _family_row(
            catalog_eligibility="not_eligible",
            model_consumption="not_used",
            why_not_used_or_sidelined="continuous_intraday_clock_out_of_scope_for_scheduled_screen",
        ),
        "latency_state": _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_used",
            why_not_used_or_sidelined="latency_feature_state_not_wired_into_vectorbt_bar_stub",
        ),
    }

    if _text(research_clock) == "continuous_intraday":
        manifest["continuous_session"] = _family_row(
            catalog_eligibility="not_measured",
            model_consumption="not_measured",
            why_not_used_or_sidelined="continuous_clock_declared_but_consumption_not_observed",
        )

    scope = _text(screening_scope)
    if scope in {"pilot", "pilot-schema-proof"}:
        for family, row in manifest.items():
            if row["model_consumption"] == "not_measured":
                row["why_not_used_or_sidelined"] = (
                    row.get("why_not_used_or_sidelined") or "pilot_scope_consumption_not_observed"
                )

    return manifest


def _manifest_family_status(manifest: Mapping[str, Any], family: str) -> str:
    row = manifest.get(family)
    if not isinstance(row, Mapping):
        return "not_measured"
    return str(row.get("model_consumption") or "not_measured")


def _all_families_consumed(manifest: Mapping[str, Any]) -> bool:
    for family in FEATURE_FAMILIES:
        if _manifest_family_status(manifest, family) != "consumed":
            return False
    return True


def classify_feature_plane_status(
    manifest: Mapping[str, Any],
    *,
    bar_construction_id: str,
    feature_set_id: str,
    feature_set_hash: str,
    research_clock: str,
    explicit_status: str | None = None,
) -> str:
    if explicit_status and str(explicit_status) in FEATURE_PLANE_STATUSES:
        return str(explicit_status)
    if _all_families_consumed(manifest):
        return FEATURE_PLANE_STATUS_FEATURE_COMPLETE
    if _is_bar_stub_path(
        bar_construction_id=bar_construction_id,
        feature_set_id=feature_set_id,
        feature_set_hash=feature_set_hash,
    ):
        return FEATURE_PLANE_STATUS_BAR_STUB
    clock = _text(research_clock)
    if clock in {"scheduled_event", "context_feature_uplift"}:
        return FEATURE_PLANE_STATUS_SCHEDULED_EVENT_ONLY
    return FEATURE_PLANE_STATUS_INCOMPLETE


def derive_model_feature_usage_status(
    manifest: Mapping[str, Any],
    feature_plane_status: str,
) -> str:
    if feature_plane_status == FEATURE_PLANE_STATUS_FEATURE_COMPLETE:
        return "pit_declared"
    consumed = [
        family
        for family in FEATURE_FAMILIES
        if _manifest_family_status(manifest, family) == "consumed"
    ]
    if consumed:
        return "partial_observed"
    return "not_observed"


def build_data_scope_skip_manifest(manifest: Mapping[str, Any]) -> dict[str, str]:
    skips: dict[str, str] = {}
    for family in FEATURE_FAMILIES:
        row = manifest.get(family)
        if not isinstance(row, Mapping):
            continue
        consumption = str(row.get("model_consumption") or "")
        if consumption.startswith("sidelined"):
            reason = str(row.get("why_not_used_or_sidelined") or consumption)
            skips[family] = reason
    return skips


def build_feature_plane_payload(
    *,
    bar_construction_id: str,
    feature_set_id: str,
    feature_set_hash: str,
    research_clock: str,
    screening_scope: str,
    target_event_type: str | None = None,
    allowed_context_set_id: str | None = None,
    declared_context_sets: Any = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit the feature-plane block required on every VectorBT screening artifact."""
    override_map = dict(overrides or {})
    manifest = build_feature_usage_manifest(
        bar_construction_id=bar_construction_id,
        feature_set_id=feature_set_id,
        feature_set_hash=feature_set_hash,
        research_clock=research_clock,
        screening_scope=screening_scope,
        overrides=override_map,
    )
    feature_plane_status = classify_feature_plane_status(
        manifest,
        bar_construction_id=bar_construction_id,
        feature_set_id=feature_set_id,
        feature_set_hash=feature_set_hash,
        research_clock=research_clock,
        explicit_status=override_map.get("feature_plane_status"),
    )
    model_feature_usage_status = str(
        override_map.get("model_feature_usage_status")
        or derive_model_feature_usage_status(manifest, feature_plane_status)
    )
    skip_manifest = build_data_scope_skip_manifest(manifest)
    data_scope_skip_manifest_hash = _hash_payload(skip_manifest) if skip_manifest else "no_dependency_scoped_skips"

    macro_status = _manifest_family_status(manifest, "macro_context")
    context_feature_coverage_status = str(
        override_map.get("context_feature_coverage_status")
        or ("not_measured" if macro_status in {"not_used", "not_measured"} else macro_status)
    )
    context_ablation_status = str(
        override_map.get("context_ablation_status") or "not_measured"
    )
    continuous_clock_status = str(
        override_map.get("continuous_clock_status")
        or ("out_of_scope" if _text(research_clock) != "continuous_intraday" else "not_measured")
    )

    payload: dict[str, Any] = {
        "feature_plane_status": feature_plane_status,
        "feature_usage_manifest": manifest,
        "feature_usage_manifest_hash": compute_feature_usage_manifest_hash(manifest),
        "model_feature_usage_status": model_feature_usage_status,
        "declared_context_sets": declared_context_sets if declared_context_sets is not None else [],
        "target_event_type_or_null": target_event_type,
        "allowed_context_set_id_or_null": allowed_context_set_id,
        "context_feature_coverage_status": context_feature_coverage_status,
        "context_ablation_status": context_ablation_status,
        "continuous_clock_status": continuous_clock_status,
        "cross_asset_alignment_status": _manifest_family_status(manifest, "cross_asset_futures"),
        "vix_sensor_status": _manifest_family_status(manifest, "vix_vvix_sensor"),
        "vix_options_status": _manifest_family_status(manifest, "vix_options"),
        "cme_options_context_status": _manifest_family_status(manifest, "cme_options_context"),
        "latency_feature_status": _manifest_family_status(manifest, "latency_state"),
        "data_scope_skip_manifest_hash": data_scope_skip_manifest_hash,
        "full_product_evidence_status": (
            "allowed"
            if feature_plane_status == FEATURE_PLANE_STATUS_FEATURE_COMPLETE
            else "refused"
        ),
    }
    for key, value in override_map.items():
        if key in FEATURE_PLANE_ARTIFACT_FIELDS and key not in {
            "feature_usage_manifest",
            "feature_usage_manifest_hash",
        }:
            # Guard: do not let an invalid explicit status override bypass the
            # deterministic classifier and manifest-based validation.
            if key == "feature_plane_status" and str(value) not in FEATURE_PLANE_STATUSES:
                continue
            payload[key] = value
    payload["feature_usage_manifest_hash"] = compute_feature_usage_manifest_hash(
        payload["feature_usage_manifest"]
    )
    return payload


def _validate_manifest_rows(errors: list[str], manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        errors.append("feature_usage_manifest_not_mapping")
        return
    for family in FEATURE_FAMILIES:
        row = manifest.get(family)
        if not isinstance(row, Mapping):
            errors.append(f"feature_usage_manifest_missing_family:{family}")
            continue
        catalog = str(row.get("catalog_eligibility") or "")
        consumption = str(row.get("model_consumption") or "")
        if catalog not in CATALOG_ELIGIBILITY_VALUES:
            errors.append(f"feature_usage_manifest_invalid_catalog_eligibility:{family}")
        if consumption not in MODEL_CONSUMPTION_VALUES:
            errors.append(f"feature_usage_manifest_invalid_model_consumption:{family}")
        if consumption == "consumed" and catalog == "not_eligible":
            errors.append(f"feature_usage_manifest_consumed_without_catalog_eligibility:{family}")
        if catalog == "eligible" and consumption == "consumed" and row.get("evidence_scope") == "catalog_eligibility_not_model_usage":
            errors.append(f"feature_usage_manifest_catalog_scope_contradicts_consumption:{family}")


def feature_plane_validation_errors(artifact: Mapping[str, Any]) -> list[str]:
    """Validate feature-plane fields and refuse mislabeled full-product evidence."""
    errors: list[str] = []
    for field_name in FEATURE_PLANE_ARTIFACT_FIELDS:
        if field_name not in artifact:
            errors.append(f"missing required field: {field_name}")
        elif artifact[field_name] is None and field_name.endswith("_or_null"):
            continue
        elif artifact[field_name] in ("", None):
            errors.append(f"missing required field: {field_name}")

    status = str(artifact.get("feature_plane_status") or "")
    if status and status not in FEATURE_PLANE_STATUSES:
        errors.append("feature_plane_status_invalid")

    manifest = artifact.get("feature_usage_manifest")
    _validate_manifest_rows(errors, manifest if isinstance(manifest, Mapping) else {})

    expected_hash = artifact.get("feature_usage_manifest_hash")
    if isinstance(manifest, Mapping) and expected_hash:
        observed_hash = compute_feature_usage_manifest_hash(manifest)
        if expected_hash != observed_hash:
            errors.append("feature_usage_manifest_hash_mismatch")

    model_usage = str(artifact.get("model_feature_usage_status") or "")
    if model_usage == "pit_declared" and status != FEATURE_PLANE_STATUS_FEATURE_COMPLETE:
        errors.append("model_feature_usage_pit_declared_without_feature_complete_status")

    full_product = str(artifact.get("full_product_evidence_status") or "")
    if status == FEATURE_PLANE_STATUS_FEATURE_COMPLETE:
        if full_product != "allowed":
            errors.append("feature_complete_requires_full_product_evidence_allowed")
        if model_usage != "pit_declared":
            errors.append("feature_complete_requires_model_feature_usage_pit_declared")
        if isinstance(manifest, Mapping) and not _all_families_consumed(manifest):
            errors.append("feature_complete_requires_all_families_consumed")
    else:
        if full_product != "refused":
            errors.append("non_feature_complete_must_refuse_full_product_evidence")
        for field_name in (
            "context_feature_coverage_status",
            "context_ablation_status",
            "continuous_clock_status",
            "cross_asset_alignment_status",
            "vix_sensor_status",
            "vix_options_status",
            "cme_options_context_status",
            "latency_feature_status",
        ):
            field_value = _text(artifact.get(field_name))
            if field_value in _MISLEADING_FULL_PRODUCT_STATUSES:
                errors.append(f"feature_plane_mislabeled_full_product:{field_name}")
        if isinstance(manifest, Mapping):
            for family in FEATURE_FAMILIES:
                if _manifest_family_status(manifest, family) == "consumed":
                    errors.append(f"feature_plane_mislabeled_consumption:{family}")

    return errors
