"""Catalog-backed feature fabric evidence for Workbench lanes."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from features_engine.src.features.registry import (
    FeatureAcceptance,
    FeatureSpec,
    load_feature_registry,
    validate_feature_registry,
    validate_model_feature_map,
)


SOURCE_TO_LANE = {
    "all_lanes": "all_lanes",
    "crypto_lane": "crypto",
    "cme_rithmic": "cme_futures",
    "equities": "equities",
    "options": "options",
    "workbench_campaign": "workbench_campaign",
    "autonomous": "autonomous",
}

ARTIFACT_NAMES = (
    "feature_fabric_manifest.json",
    "feature_lineage.json",
    "feature_pit_audit.json",
    "rejected_features.json",
)


def source_to_lane(source: str) -> str:
    return SOURCE_TO_LANE.get(source, source)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_row(
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    asset: str,
    source_symbol: str,
    feature: str,
    evidence_source: str,
    feature_id: str = "",
    feature_family: str = "",
    feature_subfamily: str = "",
    dtype: str = "",
    unit: str = "",
    shape: str = "",
    source_tier_required: str = "",
    source_tier: str = "",
    registry_status: str = "",
    acceptance_status: str = "ACCEPTED",
    acceptance_reason: str = "ACCEPTED",
    feature_index_slot: int | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    canonical_id = feature_id or feature
    row = {
        "consumer_lane": consumer_lane,
        "source_lane": source_lane,
        "asset": asset,
        "source_symbol": source_symbol,
        "feature": canonical_id,
        "feature_id": canonical_id,
        "feature_family": feature_family,
        "feature_subfamily": feature_subfamily,
        "dtype": dtype,
        "unit": unit,
        "shape": shape,
        "source_tier_required": source_tier_required,
        "source_tier": source_tier,
        "registry_status": registry_status,
        "acceptance_status": acceptance_status,
        "acceptance_reason": acceptance_reason,
        "source_available_timestamp": generated_at,
        "decision_timestamp": generated_at,
        "pit_status": "PASS",
        "pit_safe": True,
        "leakage_audit_status": "PASS",
        "evidence_scope": "catalog_eligibility_not_model_usage",
        "consumer_model": "not_observed",
        "model_feature_usage": "not_observed",
        "evidence_source": evidence_source,
    }
    if feature_index_slot is not None:
        row["feature_index_slot"] = feature_index_slot
    if run_id:
        row["run_id"] = run_id
    return row


def _reject_row(
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    asset: str,
    source_symbol: str,
    feature: str,
    reason: str,
    evidence_source: str,
    spec: FeatureSpec | None = None,
    acceptance: FeatureAcceptance | None = None,
    source_tier: str = "",
    pit_safe: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    row = _safe_row(
        generated_at=generated_at,
        consumer_lane=consumer_lane,
        source_lane=source_lane,
        asset=asset,
        source_symbol=source_symbol,
        feature=feature,
        evidence_source=evidence_source,
        feature_id=spec.feature_id if spec else feature,
        feature_family=spec.family if spec else "",
        feature_subfamily=spec.subfamily if spec else "",
        dtype=spec.dtype if spec else "",
        unit=spec.unit if spec else "",
        shape=spec.shape if spec else "",
        source_tier_required=spec.source_tier_required if spec else "",
        source_tier=source_tier,
        registry_status=spec.status if spec else "",
        acceptance_status=acceptance.status if acceptance else "REJECTED",
        acceptance_reason="; ".join(acceptance.reasons) if acceptance else reason,
        feature_index_slot=spec.feature_index_slot if spec else None,
        run_id=run_id,
    )
    row.update(
        {
            "pit_status": "REJECTED",
            "pit_safe": pit_safe,
            "leakage_audit_status": "REJECTED",
            "reject_reason": reason,
        }
    )
    return row


def _row_from_spec(
    spec: FeatureSpec,
    acceptance: FeatureAcceptance,
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    asset: str,
    source_symbol: str,
    evidence_source: str,
    source_tier: str,
    run_id: str = "",
) -> dict[str, Any]:
    return _safe_row(
        generated_at=generated_at,
        consumer_lane=consumer_lane,
        source_lane=source_lane,
        asset=asset,
        source_symbol=source_symbol,
        feature=spec.feature_id,
        evidence_source=evidence_source,
        feature_id=spec.feature_id,
        feature_family=spec.family,
        feature_subfamily=spec.subfamily,
        dtype=spec.dtype,
        unit=spec.unit,
        shape=spec.shape,
        source_tier_required=spec.source_tier_required,
        source_tier=source_tier,
        registry_status=spec.status,
        acceptance_status=acceptance.status,
        acceptance_reason="; ".join(acceptance.reasons),
        feature_index_slot=spec.feature_index_slot,
        run_id=run_id,
    )


def _reject_spec_row(
    spec: FeatureSpec,
    acceptance: FeatureAcceptance,
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    asset: str,
    source_symbol: str,
    evidence_source: str,
    source_tier: str,
    reason: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    return _reject_row(
        generated_at=generated_at,
        consumer_lane=consumer_lane,
        source_lane=source_lane,
        asset=asset,
        source_symbol=source_symbol,
        feature=spec.feature_id,
        reason=reason or "; ".join(acceptance.reasons),
        evidence_source=evidence_source,
        source_tier=source_tier,
        spec=spec,
        acceptance=acceptance,
        pit_safe="PIT_UNSAFE" not in acceptance.reasons and reason != "point_in_time_safe_false",
        run_id=run_id,
    )


def _source_tier_for_spec(spec: FeatureSpec) -> str:
    return spec.source_tier_required


def _cme_registry_rows(
    repo: Path,
    *,
    generated_at: str,
    consumer_lane: str,
    specs: list[FeatureSpec],
    run_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry = load_feature_registry()
    try:
        from workbench.src.data.instrument_registry import load_instrument_registry

        records = load_instrument_registry(repo)
    except Exception as exc:
        return [], [
            _reject_row(
                generated_at=generated_at,
                consumer_lane=consumer_lane,
                source_lane="cme_futures",
                asset="cme_catalog",
                source_symbol="cme_catalog",
                feature="cme_futures.registry_load",
                reason=f"hot_memory_universe_load_failed: {exc}",
                evidence_source="apps/workbench/config/hot_memory_universe.yaml",
                run_id=run_id,
            )
        ]
    safe: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for rec in records.values():
        asset = str(rec.asset_class or "cme_futures")
        source_symbol = str(rec.canonical_internal_symbol)
        for spec in specs:
            required_inputs_available = bool(
                rec.order_book_available
                or spec.source_domain
                in {
                    "cme_macro_event_replay",
                    "pdf_structural_models",
                    "hfc3_cross_asset_l3",
                }
            )
            acceptance = registry.accept(
                spec.feature_id,
                consumer_lane=consumer_lane,
                source_lane="cme_futures",
                model_kind="workbench_campaign",
                pit_safe=bool(rec.point_in_time_safe),
                source_tier=_source_tier_for_spec(spec),
                required_inputs_available=required_inputs_available,
            )
            if acceptance.accepted:
                safe.append(
                    _row_from_spec(
                        spec,
                        acceptance,
                        generated_at=generated_at,
                        consumer_lane=consumer_lane,
                        source_lane="cme_futures",
                        asset=asset,
                        source_symbol=source_symbol,
                        evidence_source="apps/workbench/config/hot_memory_universe.yaml",
                        source_tier=_source_tier_for_spec(spec),
                        run_id=run_id,
                    )
                )
            else:
                reason = "point_in_time_safe_false" if not rec.point_in_time_safe else "; ".join(acceptance.reasons)
                rejected.append(
                    _reject_spec_row(
                        spec,
                        acceptance,
                        generated_at=generated_at,
                        consumer_lane=consumer_lane,
                        source_lane="cme_futures",
                        asset=asset,
                        source_symbol=source_symbol,
                        reason=reason,
                        evidence_source="apps/workbench/config/hot_memory_universe.yaml",
                        source_tier=_source_tier_for_spec(spec),
                        run_id=run_id,
                    )
                )
    return safe, rejected


def _non_cme_lane_evidence(source_lane: str, config: dict[str, Any]) -> tuple[list[str], str, bool]:
    symbols = [str(symbol) for symbol in config.get("symbols") or [] if str(symbol).strip()]
    event_types = [str(event_type) for event_type in config.get("event_types") or [] if str(event_type).strip()]
    tests = [str(path) for path in config.get("test_paths") or [] if str(path).strip()]
    if source_lane == "crypto":
        venues = [str(venue) for venue in config.get("venues") or [] if str(venue).strip()]
        inputs_available = bool(
            symbols
            and event_types
            and venues
            and config.get("environment_validated") is True
            and str(config.get("environment_source_ref") or "").strip()
        )
    elif source_lane == "equities":
        inputs_available = bool(symbols and event_types and tests)
    elif source_lane == "options":
        inputs_available = bool(symbols and event_types and tests)
    else:
        inputs_available = bool(symbols and event_types and tests)
    source_tier = "tier_2_vendor_normalized" if inputs_available else "tier_4_untrusted_context"
    return symbols or [source_lane], source_tier, inputs_available


def _registry_source_rows(
    repo: Path,
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    config: dict[str, Any] | None = None,
    run_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry = load_feature_registry()
    specs = registry.specs_for_lane(source_lane)
    if not specs:
        return [], [
            _reject_row(
                generated_at=generated_at,
                consumer_lane=consumer_lane,
                source_lane=source_lane,
                asset=source_lane,
                source_symbol=source_lane,
                feature=f"{source_lane}.registry",
                reason="NO_REGISTERED_FEATURES_FOR_LANE",
                evidence_source="packages/features_engine/config/feature_registry.yaml",
                run_id=run_id,
            )
        ]
    if source_lane == "cme_futures":
        return _cme_registry_rows(
            repo,
            generated_at=generated_at,
            consumer_lane=consumer_lane,
            specs=specs,
            run_id=run_id,
        )

    safe: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    symbols, source_tier, required_inputs_available = _non_cme_lane_evidence(source_lane, config or {})
    for spec in specs:
        for symbol in symbols:
            acceptance = registry.accept(
                spec.feature_id,
                consumer_lane=consumer_lane,
                source_lane=source_lane,
                model_kind="workbench_campaign",
                pit_safe=True,
                source_tier=source_tier,
                required_inputs_available=required_inputs_available,
            )
            asset = spec.asset or source_lane
            source_symbol = spec.source_symbol or symbol
            if acceptance.accepted:
                safe.append(
                    _row_from_spec(
                        spec,
                        acceptance,
                        generated_at=generated_at,
                        consumer_lane=consumer_lane,
                        source_lane=source_lane,
                        asset=asset,
                        source_symbol=source_symbol,
                        evidence_source="packages/features_engine/config/feature_registry.yaml",
                        source_tier=source_tier,
                        run_id=run_id,
                    )
                )
            else:
                rejected.append(
                    _reject_spec_row(
                        spec,
                        acceptance,
                        generated_at=generated_at,
                        consumer_lane=consumer_lane,
                        source_lane=source_lane,
                        asset=asset,
                        source_symbol=source_symbol,
                        evidence_source="packages/features_engine/config/feature_registry.yaml",
                        source_tier=source_tier,
                        run_id=run_id,
                    )
                )
    return safe, rejected


def _lane_registry_snapshot(repo: Path) -> dict[str, Any]:
    from hft3.validation.lanes.lane_registry import LaneRegistry
    from hft3.validation.lanes.registration import register_all_lanes

    register_all_lanes()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for registration in LaneRegistry.instance().all_registrations():
        lane_value = registration.lane.value
        try:
            config = registration.config_loader()
            config_payload = config.to_dict() if hasattr(config, "to_dict") else {}
        except Exception as exc:
            config_payload = {}
            errors.append(
                {
                    "lane": lane_value,
                    "stage": "config_loader",
                    "status": "BLOCKING",
                    "error": str(exc),
                }
            )
        rows.append({"lane": lane_value, "config": config_payload})
    if not rows:
        errors.append(
            {
                "lane": "",
                "stage": "registration",
                "status": "BLOCKING",
                "error": "Lane registry returned no registered lanes.",
            }
        )
    return {
        "status": "BLOCKING" if errors else "PASS",
        "rows": rows,
        "errors": errors,
    }


def _registry_validation_gates() -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    try:
        feature_errors = validate_feature_registry()
    except Exception as exc:
        feature_errors = [f"feature registry validation raised: {exc}"]
    try:
        map_errors = validate_model_feature_map()
    except Exception as exc:
        map_errors = [f"model feature map validation raised: {exc}"]
    gates.extend(
        {
            "gate": "feature_registry",
            "status": "BLOCKING",
            "reason": str(error),
        }
        for error in feature_errors
    )
    gates.extend(
        {
            "gate": "model_feature_map",
            "status": "BLOCKING",
            "reason": str(error),
        }
        for error in map_errors
    )
    return gates


def ensure_catalog_feature_fabric(
    repo: Path,
    consumer_lane: str,
    output_root: str | Path | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Write catalog feature-fabric artifacts for a selected consumer lane."""

    root = Path(output_root) if output_root else repo / "runtime" / "workbench" / "feature_fabric"
    generated_at = _utc_now()
    lane_registry = _lane_registry_snapshot(repo)
    registry_gates = _registry_validation_gates()
    artifact_paths = {name: root / name for name in ARTIFACT_NAMES}
    blocking_gates = list(lane_registry["errors"]) + registry_gates

    if blocking_gates:
        manifest = {
            "schema_version": "feature_fabric_manifest_v1",
            "run_id": run_id,
            "generated_at_utc": generated_at,
            "consumer_lane": consumer_lane,
            "artifact_scope": "catalog_eligibility_not_model_usage",
            "lane_registry_status": lane_registry["status"],
            "feature_registry_status": "BLOCKING"
            if any(gate.get("gate") == "feature_registry" for gate in registry_gates)
            else "PASS",
            "model_feature_map_status": "BLOCKING"
            if any(gate.get("gate") == "model_feature_map" for gate in registry_gates)
            else "PASS",
            "registry_enforced": True,
            "blocking_gates": blocking_gates,
        }
        _write_json(artifact_paths["feature_fabric_manifest.json"], manifest)
        _write_json(
            artifact_paths["feature_lineage.json"],
            {"schema_version": "feature_lineage_v1", "run_id": run_id, "features": []},
        )
        _write_json(
            artifact_paths["feature_pit_audit.json"],
            {
                "schema_version": "feature_pit_audit_v1",
                "run_id": run_id,
                "generated_at_utc": generated_at,
                "pit_rule": "source_available_timestamp <= decision_timestamp",
                "rows": [],
                "pass_count": 0,
                "fail_count": len(blocking_gates),
            },
        )
        _write_json(
            artifact_paths["rejected_features.json"],
            {
                "schema_version": "rejected_features_v1",
                "run_id": run_id,
                "rows": blocking_gates,
                "rejected_count": len(blocking_gates),
            },
        )
        return {
            "status": "BLOCKING",
            "artifact_paths": {key: str(value) for key, value in artifact_paths.items()},
            "row_count": 0,
            "rejected_count": len(blocking_gates),
            "run_id": run_id,
        }

    lineage_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    lane_configs = {row["lane"]: row.get("config") or {} for row in lane_registry["rows"]}
    for source_lane in lane_configs:
        safe, rejected = _registry_source_rows(
            repo,
            generated_at=generated_at,
            consumer_lane=consumer_lane,
            source_lane=source_lane,
            config=lane_configs[source_lane],
            run_id=run_id,
        )
        lineage_rows.extend(safe)
        rejected_rows.extend(rejected)

    manifest = {
        "schema_version": "feature_fabric_manifest_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "consumer_lane": consumer_lane,
        "artifact_scope": "catalog_eligibility_not_model_usage",
        "lane_registry_status": lane_registry["status"],
        "feature_registry_status": "PASS",
        "model_feature_map_status": "PASS",
        "registry_enforced": True,
        "source_lanes": list(lane_configs),
        "model_feature_usage_status": "not_observed",
        "catalog_pit_eligibility_status": "PASS" if lineage_rows else "MISSING",
        "row_count": len(lineage_rows),
        "rejected_count": len(rejected_rows),
    }
    pit_audit = {
        "schema_version": "feature_pit_audit_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "pit_rule": "source_available_timestamp <= decision_timestamp",
        "rows": lineage_rows,
        "pass_count": len(lineage_rows),
        "fail_count": 0,
        "rejected_count": len(rejected_rows),
    }
    rejected = {
        "schema_version": "rejected_features_v1",
        "run_id": run_id,
        "rows": rejected_rows,
        "rejected_count": len(rejected_rows),
    }
    lineage = {
        "schema_version": "feature_lineage_v1",
        "run_id": run_id,
        "features": lineage_rows,
        "model_feature_usage_status": "not_observed",
    }
    _write_json(artifact_paths["feature_fabric_manifest.json"], manifest)
    _write_json(artifact_paths["feature_lineage.json"], lineage)
    _write_json(artifact_paths["feature_pit_audit.json"], pit_audit)
    _write_json(artifact_paths["rejected_features.json"], rejected)
    return {
        "status": "PASS" if lineage_rows else "BLOCKING",
        "artifact_paths": {key: str(value) for key, value in artifact_paths.items()},
        "row_count": len(lineage_rows),
        "rejected_count": len(rejected_rows),
        "run_id": run_id,
    }
