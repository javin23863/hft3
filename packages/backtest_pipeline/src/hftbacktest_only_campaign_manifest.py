"""Uniform HftBacktest-only campaign manifest builder.

The active campaign identity is the canonical descriptive slug from
``model_registry.yaml``. Legacy identifiers are recorded only as aliases.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_manifest_v1"
SUMMARY_SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_manifest_summary_v1"
PARAMETER_SURFACE_SCHEMA_VERSION = "hft3_hftbacktest_only_parameter_surface_manifest_v1"
PARAMETER_SURFACE_SUMMARY_SCHEMA_VERSION = (
    "hft3_hftbacktest_only_parameter_surface_manifest_summary_v1"
)
PRODUCT_METADATA_POLICY = "explicit_per_symbol_contract_tick_lot_contract_required"
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "features_engine"
    / "config"
    / "model_registry.yaml"
)
DEFAULT_AUTHORITY_REFS = (
    "packages/features_engine/config/model_registry.yaml",
    "docs/model_registry.md",
    "docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md",
    "docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md",
    "vault:decisions/2026-06-29 HBT-only all-model uniform-flow rule.md",
)
REQUIRED_ROW_FIELDS = (
    "campaign_id",
    "unit_id",
    "canonical_model_id",
    "legacy_aliases",
    "registry_hash",
    "source_npz",
    "source_npz_sha256",
    "symbol",
    "contract",
    "event_id",
    "event_window",
    "initial_snapshot",
    "initial_snapshot_sha256",
    "prepared_manifest",
    "tick_size",
    "lot_size",
    "contract_size",
    "product_metadata_source",
    "metadata_policy",
    "admissibility_status",
    "blocker_code",
    "blocker_detail",
    "authority_refs",
    "adapter_status",
    "hbt_run_status",
    "hbt_run_id",
    "promotion_decision_path",
)
REQUIRED_PARAMETER_SURFACE_ROW_FIELDS = (
    "campaign_id",
    "unit_id",
    "surface_unit_id",
    "canonical_model_id",
    "legacy_aliases",
    "registry_hash",
    "source_npz",
    "source_npz_sha256",
    "symbol",
    "contract",
    "event_id",
    "event_window",
    "initial_snapshot",
    "initial_snapshot_sha256",
    "prepared_manifest",
    "tick_size",
    "lot_size",
    "contract_size",
    "product_metadata_source",
    "metadata_policy",
    "parameter_family",
    "parameter_hash",
    "strategy_params",
    "parameter_proposal_status",
    "objective_evaluations",
    "optimizer_claim",
    "adapter_status",
    "admissibility_status",
    "blocker_code",
    "blocker_detail",
    "authority_refs",
    "hbt_run_status",
    "hbt_run_id",
    "recorder_result_path",
    "stats_summary_path",
    "promotion_decision_path",
)
_LEGACY_MODEL_ID_RE = re.compile(r"^(HYP_\d+|PDF_MODEL_\d+)$")
_ALLOWED_PARAMETER_FAMILIES = frozenset(
    ("grid", "bayesian-prior", "evolutionary-prior")
)
_ALLOWED_ADAPTER_STATUSES = frozenset(
    ("available", "missing_uniform_hbt_adapter", "feature_surface_mismatch")
)
_PRE_HBT_PROPOSAL_STATUS = "declared_pre_hbt"
_FORBIDDEN_PRE_HBT_DECISION_TOKENS = (
    "model_" + "rejected",
    "model_" + "untradable",
    "parameter_" + "rejected",
)


class HftBacktestOnlyCampaignManifestError(ValueError):
    """Raised when the uniform HBT campaign manifest cannot be built safely."""


def build_campaign_manifest_rows(
    *,
    campaign_id: str,
    prepared_root: Path,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    adapter_status_by_model: Mapping[str, str] | None = None,
    authority_refs: Iterable[str] = DEFAULT_AUTHORITY_REFS,
) -> list[dict[str, Any]]:
    """Build deterministic rows from canonical models x prepared HBT units."""
    registry_path = Path(registry_path)
    registry_hash = _sha256_file(registry_path)
    models = _load_canonical_models(registry_path)
    prepared_units = _load_prepared_units(Path(prepared_root))
    if not models:
        raise HftBacktestOnlyCampaignManifestError("authority_missing:model_registry_empty")
    if not prepared_units:
        raise HftBacktestOnlyCampaignManifestError(
            f"data_blocker:no_hbt_normalized_units:{prepared_root}"
        )

    adapter_statuses = (
        dict(adapter_status_by_model)
        if adapter_status_by_model is not None
        else _infer_adapter_statuses(models)
    )
    rows: list[dict[str, Any]] = []
    for canonical_model_id, entry in models:
        aliases = _legacy_aliases(entry)
        for prepared in prepared_units:
            row = _build_row(
                campaign_id=campaign_id,
                canonical_model_id=canonical_model_id,
                legacy_aliases=aliases,
                registry_hash=registry_hash,
                prepared=prepared,
                adapter_status=_adapter_status(
                    canonical_model_id,
                    adapter_statuses,
                ),
                authority_refs=tuple(authority_refs),
            )
            rows.append(row)
    validate_campaign_manifest_rows(
        rows,
        expected_canonical_model_ids=[slug for slug, _entry in models],
    )
    return rows


def write_campaign_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Write manifest JSONL plus a compact count summary."""
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(out_path, ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    os.replace(tmp, out_path)

    summary = campaign_manifest_summary(materialized)
    if summary_path is not None:
        summary_path = Path(summary_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_summary = _path_with_added_suffix(summary_path, ".tmp")
        tmp_summary.write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_summary, summary_path)
    return summary


def build_parameter_surface_rows(
    *,
    campaign_rows: Iterable[Mapping[str, Any]],
    parameter_sets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expand campaign rows by deterministic declared parameter proposals."""
    materialized_campaign_rows = [dict(row) for row in campaign_rows]
    materialized_parameter_sets = [
        _normalize_parameter_set(spec) for spec in parameter_sets
    ]
    if not materialized_campaign_rows:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")
    if not materialized_parameter_sets:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_empty")
    _validate_required_fields(
        materialized_campaign_rows,
        required_fields=REQUIRED_ROW_FIELDS,
        error_prefix="campaign_manifest",
    )

    rows: list[dict[str, Any]] = []
    for campaign_row in materialized_campaign_rows:
        for parameter_set in materialized_parameter_sets:
            parameter_hash = _parameter_hash(
                parameter_family=parameter_set["parameter_family"],
                strategy_params=parameter_set["strategy_params"],
            )
            rows.append(
                _build_parameter_surface_row(
                    campaign_row=campaign_row,
                    parameter_set=parameter_set,
                    parameter_hash=parameter_hash,
                )
            )
    validate_parameter_surface_rows(rows)
    return rows


def write_parameter_surface_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Write parameter-surface JSONL plus a compact summary."""
    materialized = [dict(row) for row in rows]
    validate_parameter_surface_rows(materialized)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(out_path, ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    os.replace(tmp, out_path)

    summary = parameter_surface_summary(materialized)
    if summary_path is not None:
        summary_path = Path(summary_path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_summary = _path_with_added_suffix(summary_path, ".tmp")
        tmp_summary.write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_summary, summary_path)
    return summary


def campaign_manifest_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = [dict(row) for row in rows]
    by_status: dict[str, int] = {}
    blocker_codes: dict[str, int] = {}
    model_ids: set[str] = set()
    source_npzs: set[str] = set()
    for row in materialized:
        status = str(row.get("admissibility_status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        blocker_code = str(row.get("blocker_code") or "")
        if blocker_code:
            blocker_codes[blocker_code] = blocker_codes.get(blocker_code, 0) + 1
        model_ids.add(str(row.get("canonical_model_id") or ""))
        source_npzs.add(str(row.get("source_npz") or ""))
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "campaign_id": materialized[0].get("campaign_id") if materialized else "",
        "row_count": len(materialized),
        "canonical_model_count": len(model_ids - {""}),
        "source_npz_count": len(source_npzs - {""}),
        "admissibility_status_counts": dict(sorted(by_status.items())),
        "blocker_code_counts": dict(sorted(blocker_codes.items())),
        "manifest_order": "registry_file_order_then_prepared_manifest_path",
    }


def parameter_surface_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = [dict(row) for row in rows]
    by_status: dict[str, int] = {}
    blocker_codes: dict[str, int] = {}
    parameter_families: dict[str, int] = {}
    objective_evaluations: dict[str, int] = {}
    model_ids: set[str] = set()
    source_npzs: set[str] = set()
    unit_ids: set[str] = set()
    parameter_hashes: set[str] = set()
    for row in materialized:
        status = str(row.get("admissibility_status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        blocker_code = str(row.get("blocker_code") or "")
        if blocker_code:
            blocker_codes[blocker_code] = blocker_codes.get(blocker_code, 0) + 1
        family = str(row.get("parameter_family") or "")
        parameter_families[family] = parameter_families.get(family, 0) + 1
        objective_count = str(row.get("objective_evaluations") or 0)
        objective_evaluations[objective_count] = (
            objective_evaluations.get(objective_count, 0) + 1
        )
        model_ids.add(str(row.get("canonical_model_id") or ""))
        source_npzs.add(str(row.get("source_npz") or ""))
        unit_ids.add(str(row.get("unit_id") or ""))
        parameter_hashes.add(str(row.get("parameter_hash") or ""))
    return {
        "schema_version": PARAMETER_SURFACE_SUMMARY_SCHEMA_VERSION,
        "campaign_id": materialized[0].get("campaign_id") if materialized else "",
        "row_count": len(materialized),
        "campaign_unit_count": len(unit_ids - {""}),
        "canonical_model_count": len(model_ids - {""}),
        "source_npz_count": len(source_npzs - {""}),
        "parameter_hash_count": len(parameter_hashes - {""}),
        "parameter_family_counts": dict(sorted(parameter_families.items())),
        "objective_evaluation_counts": dict(sorted(objective_evaluations.items())),
        "admissibility_status_counts": dict(sorted(by_status.items())),
        "blocker_code_counts": dict(sorted(blocker_codes.items())),
        "manifest_order": "campaign_manifest_order_then_parameter_set_order",
    }


def validate_campaign_manifest_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_canonical_model_ids: Iterable[str],
) -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")
    _validate_required_fields(
        materialized,
        required_fields=REQUIRED_ROW_FIELDS,
        error_prefix="campaign_manifest",
    )
    actual = {str(row["canonical_model_id"]) for row in materialized}
    expected = set(expected_canonical_model_ids)
    missing_models = sorted(expected - actual)
    if missing_models:
        joined = ",".join(missing_models)
        raise HftBacktestOnlyCampaignManifestError(
            f"campaign_manifest_missing_canonical_model_ids:{joined}"
        )
    legacy_as_active = sorted(
        model_id for model_id in actual if _LEGACY_MODEL_ID_RE.match(model_id)
    )
    if legacy_as_active:
        joined = ",".join(legacy_as_active)
        raise HftBacktestOnlyCampaignManifestError(
            f"campaign_manifest_legacy_model_id_as_active:{joined}"
        )


def validate_parameter_surface_rows(rows: Iterable[Mapping[str, Any]]) -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_empty")
    _validate_required_fields(
        materialized,
        required_fields=REQUIRED_PARAMETER_SURFACE_ROW_FIELDS,
        error_prefix="parameter_surface",
    )
    surface_unit_ids: set[str] = set()
    unit_parameter_pairs: set[tuple[str, str]] = set()
    for row in materialized:
        canonical_model_id = str(row["canonical_model_id"])
        if _LEGACY_MODEL_ID_RE.match(canonical_model_id):
            raise HftBacktestOnlyCampaignManifestError(
                f"parameter_surface_legacy_model_id_as_active:{canonical_model_id}"
            )
        surface_unit_id = str(row["surface_unit_id"])
        unit_parameter_pair = (str(row["unit_id"]), str(row["parameter_hash"]))
        if surface_unit_id in surface_unit_ids or unit_parameter_pair in unit_parameter_pairs:
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_duplicate_unit_parameter_hash"
            )
        surface_unit_ids.add(surface_unit_id)
        unit_parameter_pairs.add(unit_parameter_pair)
        if str(row["parameter_proposal_status"]) != _PRE_HBT_PROPOSAL_STATUS:
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_invalid_pre_hbt_proposal_status"
            )
        if str(row["parameter_family"]) not in _ALLOWED_PARAMETER_FAMILIES:
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_unknown_parameter_family"
            )
        objective_evaluations = row["objective_evaluations"]
        if not isinstance(objective_evaluations, int) or isinstance(
            objective_evaluations,
            bool,
        ):
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
            )
        if objective_evaluations != 0:
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
            )
        if bool(row["optimizer_claim"]):
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_optimizer_claim_without_objective_evaluations"
            )
        row_text = json.dumps(row, sort_keys=True, default=str)
        for token in _FORBIDDEN_PRE_HBT_DECISION_TOKENS:
            if token in row_text:
                raise HftBacktestOnlyCampaignManifestError(
                    "parameter_surface_pre_hbt_economic_decision"
                )
        recorder_path = str(row.get("recorder_result_path") or "")
        stats_path = str(row.get("stats_summary_path") or "")
        promotion_path = str(row.get("promotion_decision_path") or "")
        if promotion_path and (not recorder_path or not stats_path):
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_promotion_requires_hbt_artifacts"
            )


def _build_parameter_surface_row(
    *,
    campaign_row: Mapping[str, Any],
    parameter_set: Mapping[str, Any],
    parameter_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": PARAMETER_SURFACE_SCHEMA_VERSION,
        "campaign_id": campaign_row["campaign_id"],
        "unit_id": campaign_row["unit_id"],
        "surface_unit_id": _surface_unit_id(
            unit_id=str(campaign_row["unit_id"]),
            parameter_hash=parameter_hash,
        ),
        "canonical_model_id": campaign_row["canonical_model_id"],
        "legacy_aliases": list(campaign_row["legacy_aliases"]),
        "registry_hash": campaign_row["registry_hash"],
        "source_npz": campaign_row["source_npz"],
        "source_npz_sha256": campaign_row["source_npz_sha256"],
        "symbol": campaign_row["symbol"],
        "contract": campaign_row["contract"],
        "event_id": campaign_row["event_id"],
        "event_window": dict(campaign_row["event_window"]),
        "initial_snapshot": campaign_row["initial_snapshot"],
        "initial_snapshot_sha256": campaign_row["initial_snapshot_sha256"],
        "prepared_manifest": campaign_row.get("prepared_manifest", ""),
        "tick_size": campaign_row.get("tick_size"),
        "lot_size": campaign_row.get("lot_size"),
        "contract_size": campaign_row.get("contract_size"),
        "product_metadata_source": campaign_row.get("product_metadata_source", ""),
        "metadata_policy": campaign_row.get("metadata_policy", ""),
        "parameter_family": parameter_set["parameter_family"],
        "parameter_hash": parameter_hash,
        "strategy_params": dict(parameter_set["strategy_params"]),
        "parameter_proposal_status": parameter_set["parameter_proposal_status"],
        "objective_evaluations": parameter_set["objective_evaluations"],
        "optimizer_claim": parameter_set["optimizer_claim"],
        "adapter_status": campaign_row["adapter_status"],
        "admissibility_status": campaign_row["admissibility_status"],
        "blocker_code": campaign_row["blocker_code"],
        "blocker_detail": campaign_row["blocker_detail"],
        "authority_refs": list(campaign_row["authority_refs"]),
        "hbt_run_status": campaign_row["hbt_run_status"],
        "hbt_run_id": campaign_row["hbt_run_id"],
        "recorder_result_path": "",
        "stats_summary_path": "",
        "promotion_decision_path": campaign_row["promotion_decision_path"],
    }


def _build_row(
    *,
    campaign_id: str,
    canonical_model_id: str,
    legacy_aliases: list[str],
    registry_hash: str,
    prepared: Mapping[str, Any],
    adapter_status: str,
    authority_refs: tuple[str, ...],
) -> dict[str, Any]:
    source_npz = str(prepared.get("normalized_npz") or prepared.get("source_npz") or "")
    initial_snapshot = str(prepared.get("initial_snapshot") or "")
    source_path = Path(source_npz) if source_npz else None
    snapshot_path = Path(initial_snapshot) if initial_snapshot else None
    source_exists = bool(source_path and source_path.is_file())
    snapshot_exists = bool(snapshot_path and snapshot_path.is_file())
    blocker_details: list[str] = []
    blocker_code = str(prepared.get("blocker_code") or "")
    admissibility_status = "admissible"
    prepared_authority_refs = _authority_ref_tuple(prepared.get("authority_refs"))
    combined_authority_refs = _combine_authority_refs(
        authority_refs,
        prepared_authority_refs,
    )
    if blocker_code and prepared.get("blocker_detail"):
        blocker_details.append(str(prepared.get("blocker_detail")))

    if not blocker_code and not source_exists:
        blocker_code = "data_blocker:source_npz_missing"
        blocker_details.append(f"source_npz={source_npz or '<missing>'}")
    elif not blocker_code and not snapshot_exists:
        blocker_code = "data_blocker:initial_snapshot_missing"
        blocker_details.append(f"initial_snapshot={initial_snapshot or '<missing>'}")

    missing_metadata = [
        field
        for field in ("symbol", "contract", "event_id", "product_metadata_source")
        if not str(prepared.get(field) or "").strip()
    ]
    if str(prepared.get("metadata_policy") or "") != PRODUCT_METADATA_POLICY:
        missing_metadata.append("metadata_policy")
    if not prepared_authority_refs:
        missing_metadata.append("product_authority_refs")
    authority_details: list[str] = []
    if missing_metadata:
        authority_details.append("missing_metadata=" + ",".join(missing_metadata))
    if not combined_authority_refs:
        authority_details.append("authority_refs=<missing>")
    if authority_details:
        blocker_details.extend(authority_details)
        if not blocker_code:
            blocker_code = "authority_missing"

    if not blocker_code and adapter_status in {
        "missing_uniform_hbt_adapter",
        "feature_surface_mismatch",
    }:
        blocker_code = f"pipeline_blocker:{adapter_status}"
        blocker_details.append(f"canonical_model_id={canonical_model_id}")

    if blocker_code:
        admissibility_status = blocker_code.split(":", 1)[0]

    source_hash = (
        _sha256_file(source_path)
        if source_exists and source_path
        else str(prepared.get("source_npz_sha256") or "")
    )
    snapshot_hash = _sha256_file(snapshot_path) if snapshot_exists and snapshot_path else ""
    unit_id = _unit_id(
        canonical_model_id=canonical_model_id,
        source_npz=source_npz,
        source_npz_sha256=source_hash,
        initial_snapshot=initial_snapshot,
        initial_snapshot_sha256=snapshot_hash,
        symbol=str(prepared.get("symbol") or ""),
        contract=str(prepared.get("contract") or ""),
        event_id=str(prepared.get("event_id") or ""),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "unit_id": unit_id,
        "canonical_model_id": canonical_model_id,
        "legacy_aliases": legacy_aliases,
        "registry_hash": registry_hash,
        "source_npz": source_npz,
        "source_npz_sha256": source_hash,
        "symbol": str(prepared.get("symbol") or ""),
        "contract": str(prepared.get("contract") or ""),
        "event_id": str(prepared.get("event_id") or ""),
        "event_window": _event_window(prepared),
        "initial_snapshot": initial_snapshot,
        "initial_snapshot_sha256": snapshot_hash,
        "prepared_manifest": str(prepared.get("prepared_manifest") or ""),
        "tick_size": prepared.get("tick_size"),
        "lot_size": prepared.get("lot_size"),
        "contract_size": prepared.get("contract_size"),
        "product_metadata_source": str(prepared.get("product_metadata_source") or ""),
        "metadata_policy": str(prepared.get("metadata_policy") or ""),
        "admissibility_status": admissibility_status,
        "blocker_code": blocker_code,
        "blocker_detail": "; ".join(blocker_details),
        "authority_refs": list(combined_authority_refs),
        "adapter_status": adapter_status,
        "hbt_run_status": "not_started",
        "hbt_run_id": "",
        "promotion_decision_path": "",
    }


def _combine_authority_refs(
    default_refs: Iterable[str],
    prepared_refs: Any,
) -> tuple[str, ...]:
    refs: list[str] = [str(ref) for ref in default_refs if str(ref).strip()]
    if isinstance(prepared_refs, tuple):
        refs.extend(str(ref) for ref in prepared_refs if str(ref).strip())
        return tuple(dict.fromkeys(refs))
    if isinstance(prepared_refs, str):
        if prepared_refs.strip():
            refs.append(prepared_refs)
    elif isinstance(prepared_refs, IterableABC):
        refs.extend(str(ref) for ref in prepared_refs if str(ref).strip())
    return tuple(dict.fromkeys(refs))


def _authority_ref_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, IterableABC):
        return tuple(str(ref) for ref in value if str(ref).strip())
    return ()


def _normalize_parameter_set(spec: Mapping[str, Any]) -> dict[str, Any]:
    parameter_family = str(spec.get("parameter_family") or "").strip()
    if not parameter_family:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_missing_parameter_family"
        )
    if parameter_family not in _ALLOWED_PARAMETER_FAMILIES:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_unknown_parameter_family"
        )
    strategy_params = spec.get("strategy_params")
    if not isinstance(strategy_params, Mapping):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_strategy_params_must_be_object"
        )
    raw_objective_evaluations = spec.get("objective_evaluations", 0)
    if not isinstance(raw_objective_evaluations, int) or isinstance(
        raw_objective_evaluations,
        bool,
    ):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
        )
    objective_evaluations = raw_objective_evaluations
    if objective_evaluations != 0:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
        )
    status = str(spec.get("parameter_proposal_status") or _PRE_HBT_PROPOSAL_STATUS)
    if status != _PRE_HBT_PROPOSAL_STATUS:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_pre_hbt_proposal_status"
        )
    optimizer_claim = bool(spec.get("optimizer_claim", False))
    if objective_evaluations == 0 and optimizer_claim:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_optimizer_claim_without_objective_evaluations"
        )
    return {
        "parameter_family": parameter_family,
        "strategy_params": dict(strategy_params),
        "parameter_proposal_status": status,
        "objective_evaluations": objective_evaluations,
        "optimizer_claim": optimizer_claim,
    }


def _load_canonical_models(registry_path: Path) -> list[tuple[str, Mapping[str, Any]]]:
    registry = yaml.safe_load(Path(registry_path).read_text(encoding="utf-8")) or {}
    models = registry.get("models") or {}
    if not isinstance(models, Mapping):
        raise HftBacktestOnlyCampaignManifestError("authority_missing:model_registry_models")
    out: list[tuple[str, Mapping[str, Any]]] = []
    for slug, entry in models.items():
        slug_text = str(slug)
        if _LEGACY_MODEL_ID_RE.match(slug_text):
            raise HftBacktestOnlyCampaignManifestError(
                f"campaign_manifest_registry_slug_is_legacy:{slug_text}"
            )
        out.append((slug_text, dict(entry or {})))
    return out


def _load_prepared_units(prepared_root: Path) -> list[dict[str, Any]]:
    paths = sorted(Path(prepared_root).glob("**/*_manifest.json"), key=lambda path: path.as_posix())
    units: list[dict[str, Any]] = []
    for manifest_path in paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        normalized_npz = _resolve_manifest_path(manifest_path, manifest.get("normalized_npz"))
        initial_snapshot = _resolve_manifest_path(manifest_path, manifest.get("initial_snapshot"))
        units.append(
            {
                **manifest,
                "prepared_manifest": str(manifest_path),
                "symbol": manifest.get("symbol") or _symbol_from_manifest_path(manifest_path),
                "event_id": manifest.get("event_id") or _event_id_from_manifest_path(manifest_path),
                "trade_date": manifest.get("trade_date") or _trade_date_from_manifest_path(manifest_path),
                "normalized_npz": str(normalized_npz) if normalized_npz else "",
                "initial_snapshot": str(initial_snapshot) if initial_snapshot else "",
            }
        )
    return units


def _resolve_manifest_path(manifest_path: Path, value: Any) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _symbol_from_manifest_path(manifest_path: Path) -> str:
    try:
        return manifest_path.parent.parent.name
    except IndexError:
        return ""


def _trade_date_from_manifest_path(manifest_path: Path) -> str:
    return manifest_path.parent.name


def _event_id_from_manifest_path(manifest_path: Path) -> str:
    stem = manifest_path.stem
    if stem.endswith("_manifest"):
        stem = stem[: -len("_manifest")]
    if "_warmup_" in stem:
        stem = stem.split("_warmup_", 1)[0]
    return stem


def _legacy_aliases(entry: Mapping[str, Any]) -> list[str]:
    legacy_id = entry.get("legacy_id")
    if legacy_id is None or str(legacy_id).strip() == "":
        return []
    return [str(legacy_id)]


def _adapter_status(
    canonical_model_id: str,
    adapter_status_by_model: Mapping[str, str],
) -> str:
    status = str(
        adapter_status_by_model.get(canonical_model_id)
        or "missing_uniform_hbt_adapter"
    )
    if status not in _ALLOWED_ADAPTER_STATUSES:
        raise HftBacktestOnlyCampaignManifestError(
            f"campaign_manifest_invalid_adapter_status:{canonical_model_id}:{status}"
        )
    return status


def _infer_adapter_statuses(models: list[tuple[str, Mapping[str, Any]]]) -> dict[str, str]:
    try:
        from backtest_pipeline.src.hftbacktest_only_pipeline import (
            uniform_hbt_order_adapter_status,
        )
    except Exception:
        return {slug: "missing_uniform_hbt_adapter" for slug, _entry in models}
    return {slug: uniform_hbt_order_adapter_status(slug) for slug, _entry in models}


def _event_window(prepared: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(prepared.get("event_window"), Mapping):
        return dict(prepared["event_window"])
    return {
        key: prepared[key]
        for key in ("trade_date", "start_ts_ns", "cutoff_ts_ns", "end_ts_ns")
        if key in prepared
    }


def _unit_id(
    *,
    canonical_model_id: str,
    source_npz: str,
    source_npz_sha256: str,
    initial_snapshot: str,
    initial_snapshot_sha256: str,
    symbol: str,
    contract: str,
    event_id: str,
) -> str:
    seed = "|".join(
        [
            canonical_model_id,
            source_npz_sha256 or source_npz,
            initial_snapshot_sha256 or initial_snapshot,
            symbol,
            contract,
            event_id,
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"hbt_unit_{digest}"


def _surface_unit_id(*, unit_id: str, parameter_hash: str) -> str:
    return f"{unit_id}_{parameter_hash[:16]}"


def _parameter_hash(*, parameter_family: str, strategy_params: Mapping[str, Any]) -> str:
    payload = {
        "parameter_family": parameter_family,
        "strategy_params": dict(strategy_params),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HftBacktestOnlyCampaignManifestError(
            f"parameter_surface_strategy_params_not_canonical_json:{exc}"
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_with_added_suffix(path: Path, suffix: str) -> Path:
    return Path(path).with_name(Path(path).name + suffix)


def _validate_required_fields(
    rows: Iterable[Mapping[str, Any]],
    *,
    required_fields: Iterable[str],
    error_prefix: str,
) -> None:
    missing_fields = {
        field
        for row in rows
        for field in required_fields
        if field not in row
    }
    if missing_fields:
        joined = ",".join(sorted(missing_fields))
        raise HftBacktestOnlyCampaignManifestError(
            f"{error_prefix}_missing_required_fields:{joined}"
        )
