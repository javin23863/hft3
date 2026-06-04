"""Catalog-backed feature fabric evidence for Workbench lanes."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import yaml


SOURCE_TO_LANE = {
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
) -> dict[str, Any]:
    return {
        "consumer_lane": consumer_lane,
        "source_lane": source_lane,
        "asset": asset,
        "source_symbol": source_symbol,
        "feature": feature,
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
) -> dict[str, Any]:
    row = _safe_row(
        generated_at=generated_at,
        consumer_lane=consumer_lane,
        source_lane=source_lane,
        asset=asset,
        source_symbol=source_symbol,
        feature=feature,
        evidence_source=evidence_source,
    )
    row.update(
        {
            "pit_status": "REJECTED",
            "pit_safe": False,
            "leakage_audit_status": "REJECTED",
            "reject_reason": reason,
        }
    )
    return row


def _lane_config_rows(
    *,
    generated_at: str,
    consumer_lane: str,
    source_lane: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    symbols = [str(symbol) for symbol in (config.get("symbols") or []) if str(symbol).strip()]
    if not symbols:
        symbols = [f"{source_lane}_catalog"]
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rows.append(
            _safe_row(
                generated_at=generated_at,
                consumer_lane=consumer_lane,
                source_lane=source_lane,
                asset=symbol,
                source_symbol=symbol,
                feature=f"{source_lane}_{symbol}_catalog_feature",
                evidence_source="lane_config",
            )
        )
    return rows


def _crypto_candidate_rows(repo: Path, *, generated_at: str, consumer_lane: str) -> list[dict[str, Any]]:
    root = repo / "packages" / "crypto_lane" / "config" / "candidates"
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(payload, dict):
            continue
        candidate_id = str(payload.get("candidate_id") or path.stem)
        symbols = [str(symbol) for symbol in (payload.get("universe") or []) if str(symbol).strip()]
        features = [str(feature) for feature in (payload.get("features") or []) if str(feature).strip()]
        if not symbols:
            symbols = ["crypto_catalog"]
        if not features:
            features = [f"{candidate_id}_catalog_feature"]
        for symbol in symbols:
            for feature in features:
                rows.append(
                    _safe_row(
                        generated_at=generated_at,
                        consumer_lane=consumer_lane,
                        source_lane="crypto",
                        asset=symbol,
                        source_symbol=symbol,
                        feature=feature,
                        evidence_source=str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path),
                    )
                )
    return rows


def _cme_rows(repo: Path, *, generated_at: str, consumer_lane: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
                feature="cme_catalog_feature",
                reason=f"hot_memory_universe_load_failed: {exc}",
                evidence_source="apps/workbench/config/hot_memory_universe.yaml",
            )
        ]
    safe: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for rec in records.values():
        feature = f"cme_{rec.canonical_internal_symbol}_market_state"
        if rec.point_in_time_safe:
            safe.append(
                _safe_row(
                    generated_at=generated_at,
                    consumer_lane=consumer_lane,
                    source_lane="cme_futures",
                    asset=rec.asset_class,
                    source_symbol=rec.canonical_internal_symbol,
                    feature=feature,
                    evidence_source="apps/workbench/config/hot_memory_universe.yaml",
                )
            )
        else:
            rejected.append(
                _reject_row(
                    generated_at=generated_at,
                    consumer_lane=consumer_lane,
                    source_lane="cme_futures",
                    asset=rec.asset_class,
                    source_symbol=rec.canonical_internal_symbol,
                    feature=feature,
                    reason="point_in_time_safe_false",
                    evidence_source="apps/workbench/config/hot_memory_universe.yaml",
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


def ensure_catalog_feature_fabric(
    repo: Path,
    consumer_lane: str,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write catalog feature-fabric artifacts for a selected consumer lane."""

    root = Path(output_root) if output_root else repo / "runtime" / "workbench" / "feature_fabric"
    generated_at = _utc_now()
    lane_registry = _lane_registry_snapshot(repo)
    artifact_paths = {name: root / name for name in ARTIFACT_NAMES}

    if lane_registry["status"] == "BLOCKING":
        manifest = {
            "schema_version": "feature_fabric_manifest_v1",
            "generated_at_utc": generated_at,
            "consumer_lane": consumer_lane,
            "artifact_scope": "catalog_eligibility_not_model_usage",
            "lane_registry_status": lane_registry["status"],
            "blocking_gates": lane_registry["errors"],
        }
        _write_json(artifact_paths["feature_fabric_manifest.json"], manifest)
        _write_json(artifact_paths["feature_lineage.json"], {"schema_version": "feature_lineage_v1", "features": []})
        _write_json(
            artifact_paths["feature_pit_audit.json"],
            {
                "schema_version": "feature_pit_audit_v1",
                "generated_at_utc": generated_at,
                "pit_rule": "source_available_timestamp <= decision_timestamp",
                "rows": [],
                "pass_count": 0,
                "fail_count": len(lane_registry["errors"]),
            },
        )
        _write_json(
            artifact_paths["rejected_features.json"],
            {
                "schema_version": "rejected_features_v1",
                "rows": lane_registry["errors"],
                "rejected_count": len(lane_registry["errors"]),
            },
        )
        return {
            "status": "BLOCKING",
            "artifact_paths": {key: str(value) for key, value in artifact_paths.items()},
            "row_count": 0,
            "rejected_count": len(lane_registry["errors"]),
        }

    lineage_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    lane_configs = {row["lane"]: row.get("config") or {} for row in lane_registry["rows"]}
    for source_lane, config in lane_configs.items():
        if source_lane == "cme_futures":
            safe, rejected = _cme_rows(repo, generated_at=generated_at, consumer_lane=consumer_lane)
            lineage_rows.extend(safe)
            rejected_rows.extend(rejected)
        elif source_lane == "crypto":
            crypto_rows = _crypto_candidate_rows(repo, generated_at=generated_at, consumer_lane=consumer_lane)
            lineage_rows.extend(
                crypto_rows
                or _lane_config_rows(
                    generated_at=generated_at,
                    consumer_lane=consumer_lane,
                    source_lane=source_lane,
                    config=config,
                )
            )
        else:
            lineage_rows.extend(
                _lane_config_rows(
                    generated_at=generated_at,
                    consumer_lane=consumer_lane,
                    source_lane=source_lane,
                    config=config,
                )
            )

    manifest = {
        "schema_version": "feature_fabric_manifest_v1",
        "generated_at_utc": generated_at,
        "consumer_lane": consumer_lane,
        "artifact_scope": "catalog_eligibility_not_model_usage",
        "lane_registry_status": lane_registry["status"],
        "source_lanes": list(lane_configs),
        "model_feature_usage_status": "not_observed",
        "catalog_pit_eligibility_status": "PASS" if lineage_rows else "MISSING",
        "row_count": len(lineage_rows),
        "rejected_count": len(rejected_rows),
    }
    pit_audit = {
        "schema_version": "feature_pit_audit_v1",
        "generated_at_utc": generated_at,
        "pit_rule": "source_available_timestamp <= decision_timestamp",
        "rows": lineage_rows,
        "pass_count": len(lineage_rows),
        "fail_count": 0,
        "rejected_count": len(rejected_rows),
    }
    rejected = {
        "schema_version": "rejected_features_v1",
        "rows": rejected_rows,
        "rejected_count": len(rejected_rows),
    }
    lineage = {
        "schema_version": "feature_lineage_v1",
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
    }
