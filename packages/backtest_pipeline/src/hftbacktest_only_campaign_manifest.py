"""Uniform HftBacktest-only campaign manifest builder.

The active campaign identity is the canonical descriptive slug from
``model_registry.yaml``. Legacy identifiers are recorded only as aliases.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import yaml

from hft3.hbt_parameter_sets import (
    HBT_PARAMETER_SET_PRE_HBT_STATUS as _PRE_HBT_PROPOSAL_STATUS,
    HBT_PARAMETER_SET_SCHEMA_VERSION as HBT_SELF_LEARNING_PARAMETER_SET_SCHEMA_VERSION,
    HBT_PARAMETER_SET_SOURCE as HBT_SELF_LEARNING_PARAMETER_SET_SOURCE,
)


SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_manifest_v1"
SUMMARY_SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_manifest_summary_v1"
PRE_EXECUTION_SUMMARY_SCHEMA_VERSION = "hft3_hbt_only_pre_execution_campaign_summary_v1"
PARAMETER_SURFACE_SCHEMA_VERSION = "hft3_hftbacktest_only_parameter_surface_manifest_v1"
PARAMETER_SURFACE_SUMMARY_SCHEMA_VERSION = (
    "hft3_hftbacktest_only_parameter_surface_manifest_summary_v1"
)
CANARY_SUMMARY_SCHEMA_VERSION = "hft3_hftbacktest_only_canary_manifest_summary_v1"
HBT_SELF_LEARNING_PARAMETER_SET_AUTHORITY_REFS = (
    "packages/research_pipeline/parameter_search.py",
    "packages/research_pipeline/model_generation.py",
    "packages/research_pipeline/elite_refinement.py",
    "packages/research_pipeline/generation_loop.py",
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
    "source_candidate_id",
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
_FORBIDDEN_PRE_HBT_DECISION_TOKENS = (
    "model_" + "rejected",
    "model_" + "untradable",
    "parameter_" + "rejected",
)
DEFAULT_CHECKPOINT_EVERY_ROWS = 100_000
READY_ADAPTER_STATUSES = ("available", "ready")


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
    inputs = _campaign_manifest_inputs(
        campaign_id=campaign_id,
        prepared_root=prepared_root,
        registry_path=registry_path,
        adapter_status_by_model=adapter_status_by_model,
        authority_refs=authority_refs,
    )
    rows = list(_iter_campaign_rows_from_inputs(inputs))
    validate_campaign_manifest_rows(
        rows,
        expected_canonical_model_ids=inputs["canonical_model_ids"],
    )
    return rows


def iter_campaign_manifest_rows(
    *,
    campaign_id: str,
    prepared_root: Path,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    adapter_status_by_model: Mapping[str, str] | None = None,
    authority_refs: Iterable[str] = DEFAULT_AUTHORITY_REFS,
) -> Iterator[dict[str, Any]]:
    """Yield deterministic rows without materializing the campaign universe."""
    inputs = _campaign_manifest_inputs(
        campaign_id=campaign_id,
        prepared_root=prepared_root,
        registry_path=registry_path,
        adapter_status_by_model=adapter_status_by_model,
        authority_refs=authority_refs,
    )
    yield from _iter_campaign_rows_from_inputs(inputs)


def stream_campaign_manifest(
    *,
    campaign_id: str,
    prepared_root: Path,
    out_path: Path,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    adapter_status_by_model: Mapping[str, str] | None = None,
    summary_path: Path | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every_rows: int = DEFAULT_CHECKPOINT_EVERY_ROWS,
    authority_refs: Iterable[str] = DEFAULT_AUTHORITY_REFS,
    parameter_surface_status: str = "base_only",
    parameter_surface_config_status: str = "",
    parameter_sets_json: Path | None = None,
) -> dict[str, Any]:
    """Stream the base campaign manifest to JSONL and write pre-execution proof."""
    inputs = _campaign_manifest_inputs(
        campaign_id=campaign_id,
        prepared_root=prepared_root,
        registry_path=registry_path,
        adapter_status_by_model=adapter_status_by_model,
        authority_refs=authority_refs,
    )
    return write_campaign_manifest(
        _iter_campaign_rows_from_inputs(inputs),
        out_path=out_path,
        summary_path=summary_path,
        expected_canonical_model_ids=inputs["canonical_model_ids"],
        prepared_unit_count=len(inputs["prepared_units"]),
        executable_unit_count=_executable_prepared_unit_count(inputs["prepared_units"]),
        blocker_unit_count=_blocker_prepared_unit_count(inputs["prepared_units"]),
        model_applicability_counts=_model_applicability_counts(
            inputs["canonical_model_ids"],
            inputs["adapter_statuses"],
        ),
        instrument_applicability_counts=_instrument_applicability_counts(
            inputs["prepared_units"],
        ),
        checkpoint_path=checkpoint_path,
        checkpoint_every_rows=checkpoint_every_rows,
        parameter_surface_status=parameter_surface_status,
        parameter_surface_config_status=parameter_surface_config_status,
        parameter_sets_json=parameter_sets_json,
    )


def write_campaign_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    summary_path: Path | None = None,
    expected_canonical_model_ids: Iterable[str] | None = None,
    prepared_unit_count: int | None = None,
    executable_unit_count: int | None = None,
    blocker_unit_count: int | None = None,
    model_applicability_counts: Mapping[str, int] | None = None,
    instrument_applicability_counts: Mapping[str, int] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every_rows: int = DEFAULT_CHECKPOINT_EVERY_ROWS,
    parameter_surface_status: str = "base_only",
    parameter_surface_config_status: str = "",
    parameter_sets_json: Path | None = None,
) -> dict[str, Any]:
    """Write manifest JSONL plus a compact count summary without row materialization."""
    if checkpoint_every_rows < 0:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_invalid_checkpoint_every")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_output = Path(checkpoint_path) if checkpoint_path is not None else None
    tmp = _path_with_added_suffix(out_path, ".tmp")
    expected_models = set(expected_canonical_model_ids or [])
    summary_builder = _CampaignManifestSummaryBuilder(
        expected_canonical_model_ids=expected_models,
        prepared_unit_count=prepared_unit_count,
        executable_unit_count=executable_unit_count,
        blocker_unit_count=blocker_unit_count,
        model_applicability_counts=model_applicability_counts,
        instrument_applicability_counts=instrument_applicability_counts,
        parameter_surface_status=parameter_surface_status,
        parameter_surface_config_status=parameter_surface_config_status,
        parameter_sets_json=parameter_sets_json,
    )
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            for raw_row in rows:
                row = dict(raw_row)
                _validate_required_row_fields(
                    row,
                    required_fields=REQUIRED_ROW_FIELDS,
                    error_prefix="campaign_manifest",
                )
                summary_builder.observe(row)
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                if (
                    checkpoint_output is not None
                    and checkpoint_every_rows
                    and summary_builder.emitted_base_rows % checkpoint_every_rows == 0
                ):
                    handle.flush()
                    _write_json_atomic(
                        checkpoint_output,
                        summary_builder.summary(partial=True),
                    )
            if summary_builder.emitted_base_rows == 0:
                raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")
            handle.flush()
        summary_builder.validate_complete()
    except Exception:
        _cleanup_partial_outputs(tmp, checkpoint_output)
        raise
    os.replace(tmp, out_path)

    summary = summary_builder.summary(partial=False)
    if summary_path is not None:
        summary_path = Path(summary_path)
        _write_json_atomic(summary_path, summary)
    if checkpoint_output is not None:
        _write_json_atomic(checkpoint_output, summary)
    return summary


def stream_first_eligible_canary_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    count: int,
    summary_path: Path | None = None,
    source_manifest: Path | str = "",
    parameter_surface_status: str = "base_only",
    parameter_surface_config_status: str = "",
) -> dict[str, Any]:
    """Write the first N executable rows from manifest order without preference filters."""
    if count <= 0:
        raise HftBacktestOnlyCampaignManifestError("canary_manifest_count_must_be_positive")
    if not _canary_parameter_surface_status_ok(
        parameter_surface_status=parameter_surface_status,
        parameter_surface_config_status=parameter_surface_config_status,
    ):
        raise HftBacktestOnlyCampaignManifestError(
            "canary_manifest_parameter_surface_status_not_ready"
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(out_path, ".tmp")
    rows_scanned = 0
    emitted = 0
    eligibility_counts: dict[str, int] = {}
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            for raw_row in rows:
                rows_scanned += 1
                row = dict(raw_row)
                _validate_required_row_fields(
                    row,
                    required_fields=REQUIRED_ROW_FIELDS,
                    error_prefix="campaign_manifest",
                )
                eligible, reason = _canary_row_eligibility_reason(row)
                eligibility_counts[reason] = eligibility_counts.get(reason, 0) + 1
                if not eligible:
                    continue
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                emitted += 1
                if emitted == count:
                    break
            handle.flush()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if emitted != count:
        tmp.unlink(missing_ok=True)
        raise HftBacktestOnlyCampaignManifestError(
            f"canary_manifest_insufficient_eligible_rows:requested={count}:emitted={emitted}"
        )
    os.replace(tmp, out_path)
    summary = {
        "schema_version": CANARY_SUMMARY_SCHEMA_VERSION,
        "source_manifest": str(source_manifest),
        "canary_manifest": str(out_path),
        "selector": "first_n_manifest_order_after_readiness_predicates",
        "requested_count": count,
        "emitted_count": emitted,
        "source_rows_scanned": rows_scanned,
        "eligibility_counts": dict(sorted(eligibility_counts.items())),
        "readiness_predicates": {
            "data_admissible": "admissibility_status == admissible",
            "adapter_status": list(READY_ADAPTER_STATUSES),
            "authority_status": "authority_refs present and blocker_code empty",
            "parameter_surface_status": "base_only or parameter_config_present",
        },
        "manual_filter_used": False,
        "vectorbt_dependency": False,
        "stage_a_dependency": False,
        "screening_artifact_dependency": False,
        "hbt_jobs_started": 0,
        "parameter_surface_status": parameter_surface_status,
        "parameter_surface_config_status": parameter_surface_config_status,
        "manifest_order": "source_manifest_order",
    }
    if summary_path is not None:
        _write_json_atomic(Path(summary_path), summary)
    return summary


def build_parameter_surface_rows(
    *,
    campaign_rows: Iterable[Mapping[str, Any]],
    parameter_sets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expand campaign rows by deterministic declared parameter proposals."""
    materialized_campaign_rows = [dict(row) for row in campaign_rows]
    materialized_parameter_sets = _normalize_parameter_sets(parameter_sets)
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
        applied = False
        for parameter_set in materialized_parameter_sets:
            if not _parameter_set_applies_to_row(parameter_set, campaign_row):
                continue
            applied = True
            parameter_hash = _parameter_hash(
                canonical_model_id=str(parameter_set.get("canonical_model_id") or ""),
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
        _raise_if_missing_executable_parameter_set(campaign_row, applied=applied)
    validate_parameter_surface_rows(rows)
    return rows


def iter_parameter_surface_rows(
    *,
    campaign_rows: Iterable[Mapping[str, Any]],
    parameter_sets: Iterable[Mapping[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Yield parameter-surface rows without materializing the base campaign."""
    materialized_parameter_sets = _normalize_parameter_sets(parameter_sets)
    if not materialized_parameter_sets:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_empty")
    saw_campaign_row = False
    for campaign_row in campaign_rows:
        saw_campaign_row = True
        _validate_required_row_fields(
            campaign_row,
            required_fields=REQUIRED_ROW_FIELDS,
            error_prefix="campaign_manifest",
        )
        applied = False
        for parameter_set in materialized_parameter_sets:
            if not _parameter_set_applies_to_row(parameter_set, campaign_row):
                continue
            applied = True
            parameter_hash = _parameter_hash(
                canonical_model_id=str(parameter_set.get("canonical_model_id") or ""),
                parameter_family=parameter_set["parameter_family"],
                strategy_params=parameter_set["strategy_params"],
            )
            yield _build_parameter_surface_row(
                campaign_row=campaign_row,
                parameter_set=parameter_set,
                parameter_hash=parameter_hash,
            )
        _raise_if_missing_executable_parameter_set(campaign_row, applied=applied)
    if not saw_campaign_row:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")


def write_parameter_surface_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """Write parameter-surface JSONL plus a compact summary."""
    return stream_parameter_surface_manifest(
        rows,
        out_path=out_path,
        summary_path=summary_path,
    )


def stream_parameter_surface_manifest(
    rows: Iterable[Mapping[str, Any]],
    *,
    out_path: Path,
    summary_path: Path | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_every_rows: int = DEFAULT_CHECKPOINT_EVERY_ROWS,
) -> dict[str, Any]:
    """Stream parameter-surface JSONL without materializing the base campaign."""
    if checkpoint_every_rows < 0:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_invalid_checkpoint_every")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_output = Path(checkpoint_path) if checkpoint_path is not None else None
    tmp = _path_with_added_suffix(out_path, ".tmp")
    sqlite_path = _path_with_added_suffix(out_path, ".dedupe.sqlite.tmp")
    summary: dict[str, Any] | None = None
    try:
        with _SqliteUniqueTracker(sqlite_path) as unique_tracker:
            summary_builder = _ParameterSurfaceSummaryBuilder(unique_tracker=unique_tracker)
            with tmp.open("w", encoding="utf-8") as handle:
                for raw_row in rows:
                    row = dict(raw_row)
                    _validate_parameter_surface_row(row)
                    summary_builder.observe(row)
                    handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                    if (
                        checkpoint_output is not None
                        and checkpoint_every_rows
                        and summary_builder.row_count % checkpoint_every_rows == 0
                    ):
                        handle.flush()
                        _write_json_atomic(
                            checkpoint_output,
                            summary_builder.summary(partial=True),
                        )
                if summary_builder.row_count == 0:
                    raise HftBacktestOnlyCampaignManifestError("parameter_surface_empty")
                handle.flush()
                summary = summary_builder.summary(partial=False)
        os.replace(tmp, out_path)
    except Exception:
        _cleanup_partial_outputs(tmp, checkpoint_output)
        raise
    if summary is None:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_summary_missing")
    if summary_path is not None:
        _write_json_atomic(Path(summary_path), summary)
    if checkpoint_output is not None:
        _write_json_atomic(checkpoint_output, summary)
    return summary


def campaign_manifest_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    campaign_id = ""
    row_count = 0
    by_status: dict[str, int] = {}
    blocker_codes: dict[str, int] = {}
    model_ids: set[str] = set()
    source_npzs: set[str] = set()
    for raw_row in rows:
        row = dict(raw_row)
        if not campaign_id:
            campaign_id = str(row.get("campaign_id") or "")
        row_count += 1
        status = str(row.get("admissibility_status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        blocker_code = str(row.get("blocker_code") or "")
        if blocker_code:
            blocker_codes[blocker_code] = blocker_codes.get(blocker_code, 0) + 1
        model_ids.add(str(row.get("canonical_model_id") or ""))
        source_npzs.add(str(row.get("source_npz") or ""))
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "row_count": row_count,
        "canonical_model_count": len(model_ids - {""}),
        "source_npz_count": len(source_npzs - {""}),
        "admissibility_status_counts": dict(sorted(by_status.items())),
        "blocker_code_counts": dict(sorted(blocker_codes.items())),
        "manifest_order": "registry_file_order_then_prepared_manifest_path",
    }


def parameter_surface_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    builder = _ParameterSurfaceSummaryBuilder()
    for raw_row in rows:
        builder.observe(dict(raw_row))
    summary = builder.summary(partial=False)
    summary.pop("partial", None)
    return summary


class _CampaignManifestSummaryBuilder:
    def __init__(
        self,
        *,
        expected_canonical_model_ids: set[str],
        prepared_unit_count: int | None,
        executable_unit_count: int | None,
        blocker_unit_count: int | None,
        model_applicability_counts: Mapping[str, int] | None,
        instrument_applicability_counts: Mapping[str, int] | None,
        parameter_surface_status: str,
        parameter_surface_config_status: str,
        parameter_sets_json: Path | None,
    ) -> None:
        self.expected_canonical_model_ids = set(expected_canonical_model_ids)
        self.prepared_unit_count = prepared_unit_count
        self.executable_unit_count = executable_unit_count
        self.blocker_unit_count = blocker_unit_count
        self.model_applicability_counts = dict(model_applicability_counts or {})
        self.instrument_applicability_counts = dict(instrument_applicability_counts or {})
        self.parameter_surface_status = parameter_surface_status
        self.parameter_surface_config_status = parameter_surface_config_status
        self.parameter_sets_json = str(parameter_sets_json or "")
        self.campaign_id = ""
        self.emitted_base_rows = 0
        self.admissibility_status_counts: dict[str, int] = {}
        self.blocker_code_counts: dict[str, int] = {}
        self.adapter_status_counts: dict[str, int] = {}
        self.authority_missing_counts: dict[str, int] = {}
        self.model_ids: set[str] = set()
        self.source_npzs: set[str] = set()

    def observe(self, row: Mapping[str, Any]) -> None:
        if not self.campaign_id:
            self.campaign_id = str(row.get("campaign_id") or "")
        self.emitted_base_rows += 1
        canonical_model_id = str(row.get("canonical_model_id") or "")
        if _LEGACY_MODEL_ID_RE.match(canonical_model_id):
            raise HftBacktestOnlyCampaignManifestError(
                f"campaign_manifest_legacy_model_id_as_active:{canonical_model_id}"
            )
        if canonical_model_id:
            self.model_ids.add(canonical_model_id)
        source_npz = str(row.get("source_npz") or "")
        if source_npz:
            self.source_npzs.add(source_npz)
        self._bump(
            self.admissibility_status_counts,
            str(row.get("admissibility_status") or ""),
        )
        blocker_code = str(row.get("blocker_code") or "")
        if blocker_code:
            self._bump(self.blocker_code_counts, blocker_code)
        adapter_status = str(row.get("adapter_status") or "")
        self._bump(self.adapter_status_counts, adapter_status)
        if blocker_code.startswith("authority_missing"):
            self._bump(self.authority_missing_counts, blocker_code)

    def validate_complete(self) -> None:
        expected = self.expected_canonical_model_ids
        if not expected:
            return
        missing_models = sorted(expected - self.model_ids)
        if missing_models:
            joined = ",".join(missing_models)
            raise HftBacktestOnlyCampaignManifestError(
                f"campaign_manifest_missing_canonical_model_ids:{joined}"
            )
        expected_rows = self._expected_base_rows()
        if self.emitted_base_rows != expected_rows:
            raise HftBacktestOnlyCampaignManifestError(
                "campaign_manifest_row_count_mismatch:"
                f"expected={expected_rows}:emitted={self.emitted_base_rows}"
            )

    def summary(self, *, partial: bool) -> dict[str, Any]:
        canonical_model_count = self._canonical_model_count()
        prepared_unit_count = self._prepared_unit_count()
        expected_base_rows = self._expected_base_rows()
        executable_unit_count = (
            int(self.executable_unit_count)
            if self.executable_unit_count is not None
            else int(self.admissibility_status_counts.get("admissible", 0))
        )
        blocker_unit_count = (
            int(self.blocker_unit_count)
            if self.blocker_unit_count is not None
            else max(prepared_unit_count - executable_unit_count, 0)
        )
        return {
            "schema_version": PRE_EXECUTION_SUMMARY_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "row_count": self.emitted_base_rows,
            "canonical_model_count": canonical_model_count,
            "prepared_unit_count": prepared_unit_count,
            "source_npz_count": len(self.source_npzs),
            "executable_unit_count": executable_unit_count,
            "blocker_unit_count": blocker_unit_count,
            "expected_base_rows": expected_base_rows,
            "emitted_base_rows": self.emitted_base_rows,
            "row_count_matches_expected": self.emitted_base_rows == expected_base_rows,
            "adapter_status_counts": dict(sorted(self.adapter_status_counts.items())),
            "authority_missing_counts": dict(sorted(self.authority_missing_counts.items())),
            "model_applicability_counts": dict(sorted(self.model_applicability_counts.items())),
            "instrument_applicability_counts": dict(
                sorted(self.instrument_applicability_counts.items())
            ),
            "admissibility_status_counts": dict(
                sorted(self.admissibility_status_counts.items())
            ),
            "blocker_code_counts": dict(sorted(self.blocker_code_counts.items())),
            "manual_filter_used": False,
            "vectorbt_dependency": False,
            "stage_a_dependency": False,
            "screening_artifact_dependency": False,
            "hbt_jobs_started": 0,
            "parameter_surface_status": self.parameter_surface_status,
            "parameter_surface_config_status": self.parameter_surface_config_status,
            "parameter_sets_json": self.parameter_sets_json,
            "manifest_order": "registry_file_order_then_prepared_manifest_path",
            "partial": partial,
        }

    @staticmethod
    def _bump(counts: dict[str, int], key: str) -> None:
        if key:
            counts[key] = counts.get(key, 0) + 1

    def _canonical_model_count(self) -> int:
        return len(self.expected_canonical_model_ids or self.model_ids)

    def _prepared_unit_count(self) -> int:
        if self.prepared_unit_count is not None:
            return int(self.prepared_unit_count)
        return len(self.source_npzs)

    def _expected_base_rows(self) -> int:
        return self._canonical_model_count() * self._prepared_unit_count()


class _ParameterSurfaceSummaryBuilder:
    def __init__(self, unique_tracker: "_SqliteUniqueTracker | None" = None) -> None:
        self.unique_tracker = unique_tracker
        self.campaign_id = ""
        self.row_count = 0
        self.admissibility_status_counts: dict[str, int] = {}
        self.blocker_code_counts: dict[str, int] = {}
        self.parameter_family_counts: dict[str, int] = {}
        self.objective_evaluation_counts: dict[str, int] = {}
        self._unique_sets: dict[str, set[str]] = {}

    def observe(self, row: Mapping[str, Any]) -> None:
        if not self.campaign_id:
            self.campaign_id = str(row.get("campaign_id") or "")
        surface_unit_id = str(row["surface_unit_id"])
        unit_id = str(row.get("unit_id") or "")
        parameter_hash = str(row.get("parameter_hash") or "")
        unit_parameter_pair = f"{unit_id}\x1f{parameter_hash}"
        self._add_unique(
            "surface_unit_id",
            surface_unit_id,
            duplicate_error="parameter_surface_duplicate_unit_parameter_hash",
        )
        self._add_unique(
            "unit_parameter_pair",
            unit_parameter_pair,
            duplicate_error="parameter_surface_duplicate_unit_parameter_hash",
        )
        self.row_count += 1
        self._bump(
            self.admissibility_status_counts,
            str(row.get("admissibility_status") or ""),
        )
        blocker_code = str(row.get("blocker_code") or "")
        if blocker_code:
            self._bump(self.blocker_code_counts, blocker_code)
        self._bump(self.parameter_family_counts, str(row.get("parameter_family") or ""))
        self._bump(
            self.objective_evaluation_counts,
            str(row.get("objective_evaluations") or 0),
        )
        self._add_unique("canonical_model_id", str(row.get("canonical_model_id") or ""))
        self._add_unique("source_npz", str(row.get("source_npz") or ""))
        self._add_unique("unit_id", unit_id)
        self._add_unique("parameter_hash", parameter_hash)

    def summary(self, *, partial: bool) -> dict[str, Any]:
        return {
            "schema_version": PARAMETER_SURFACE_SUMMARY_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "row_count": self.row_count,
            "campaign_unit_count": self._unique_count("unit_id"),
            "canonical_model_count": self._unique_count("canonical_model_id"),
            "source_npz_count": self._unique_count("source_npz"),
            "parameter_hash_count": self._unique_count("parameter_hash"),
            "parameter_family_counts": dict(sorted(self.parameter_family_counts.items())),
            "objective_evaluation_counts": dict(
                sorted(self.objective_evaluation_counts.items())
            ),
            "admissibility_status_counts": dict(
                sorted(self.admissibility_status_counts.items())
            ),
            "blocker_code_counts": dict(sorted(self.blocker_code_counts.items())),
            "manifest_order": "campaign_manifest_order_then_parameter_set_order",
            "partial": partial,
        }

    @staticmethod
    def _bump(counts: dict[str, int], key: str) -> None:
        if key:
            counts[key] = counts.get(key, 0) + 1

    def _add_unique(
        self,
        kind: str,
        value: str,
        *,
        duplicate_error: str | None = None,
    ) -> None:
        if self.unique_tracker is not None:
            self.unique_tracker.add_unique(kind, value, duplicate_error=duplicate_error)
            return
        values = self._unique_sets.setdefault(kind, set())
        if value in values and duplicate_error is not None:
            raise HftBacktestOnlyCampaignManifestError(duplicate_error)
        values.add(value)

    def _unique_count(self, kind: str) -> int:
        if self.unique_tracker is not None:
            return self.unique_tracker.count(kind, exclude_empty=True)
        return len(self._unique_sets.get(kind, set()) - {""})


class _SqliteUniqueTracker:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "_SqliteUniqueTracker":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            "create table seen ("
            "kind text not null, "
            "value text not null, "
            "primary key (kind, value)"
            ") without rowid"
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.path.exists():
            self.path.unlink()

    def add_unique(
        self,
        kind: str,
        value: str,
        *,
        duplicate_error: str | None = None,
    ) -> None:
        if self.conn is None:
            raise HftBacktestOnlyCampaignManifestError("unique_tracker_not_open")
        try:
            self.conn.execute(
                "insert into seen(kind, value) values (?, ?)",
                (kind, value),
            )
        except sqlite3.IntegrityError as exc:
            if duplicate_error is not None:
                raise HftBacktestOnlyCampaignManifestError(duplicate_error) from exc

    def count(self, kind: str, *, exclude_empty: bool) -> int:
        if self.conn is None:
            raise HftBacktestOnlyCampaignManifestError("unique_tracker_not_open")
        if exclude_empty:
            cursor = self.conn.execute(
                "select count(*) from seen where kind = ? and value != ''",
                (kind,),
            )
        else:
            cursor = self.conn.execute(
                "select count(*) from seen where kind = ?",
                (kind,),
            )
        return int(cursor.fetchone()[0])


def _canary_parameter_surface_status_ok(
    *,
    parameter_surface_status: str,
    parameter_surface_config_status: str,
) -> bool:
    return (
        parameter_surface_status == "base_only"
        or parameter_surface_config_status == "parameter_config_present"
    )


def _canary_row_eligibility_reason(row: Mapping[str, Any]) -> tuple[bool, str]:
    if str(row.get("admissibility_status") or "") != "admissible":
        return False, "data_not_admissible"
    if str(row.get("blocker_code") or ""):
        return False, "blocker_present"
    if str(row.get("adapter_status") or "") not in READY_ADAPTER_STATUSES:
        return False, "adapter_not_ready"
    authority_refs = row.get("authority_refs")
    if not isinstance(authority_refs, list) or not authority_refs:
        return False, "authority_not_pass"
    return True, "eligible"


def validate_campaign_manifest_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_canonical_model_ids: Iterable[str],
) -> None:
    actual: set[str] = set()
    saw_row = False
    for raw_row in rows:
        saw_row = True
        row = dict(raw_row)
        _validate_required_row_fields(
            row,
            required_fields=REQUIRED_ROW_FIELDS,
            error_prefix="campaign_manifest",
        )
        actual.add(str(row["canonical_model_id"]))
    if not saw_row:
        raise HftBacktestOnlyCampaignManifestError("campaign_manifest_empty")
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
    saw_row = False
    with tempfile.TemporaryDirectory(prefix="hft3_parameter_surface_validate_") as tmp_dir:
        sqlite_path = Path(tmp_dir) / "unique.sqlite"
        with _SqliteUniqueTracker(sqlite_path) as unique_tracker:
            for raw_row in rows:
                saw_row = True
                row = dict(raw_row)
                _validate_parameter_surface_row(row)
                unit_parameter_pair = (
                    f"{row['unit_id']}\x1f{row['parameter_hash']}"
                )
                unique_tracker.add_unique(
                    "surface_unit_id",
                    str(row["surface_unit_id"]),
                    duplicate_error="parameter_surface_duplicate_unit_parameter_hash",
                )
                unique_tracker.add_unique(
                    "unit_parameter_pair",
                    unit_parameter_pair,
                    duplicate_error="parameter_surface_duplicate_unit_parameter_hash",
                )
    if not saw_row:
        raise HftBacktestOnlyCampaignManifestError("parameter_surface_empty")


def _validate_parameter_surface_row(row: Mapping[str, Any]) -> None:
    _validate_required_row_fields(
        row,
        required_fields=REQUIRED_PARAMETER_SURFACE_ROW_FIELDS,
        error_prefix="parameter_surface",
    )
    canonical_model_id = str(row["canonical_model_id"])
    if _LEGACY_MODEL_ID_RE.match(canonical_model_id):
        raise HftBacktestOnlyCampaignManifestError(
            f"parameter_surface_legacy_model_id_as_active:{canonical_model_id}"
        )
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


def _campaign_manifest_inputs(
    *,
    campaign_id: str,
    prepared_root: Path,
    registry_path: Path,
    adapter_status_by_model: Mapping[str, str] | None,
    authority_refs: Iterable[str],
) -> dict[str, Any]:
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
    canonical_model_ids = [slug for slug, _entry in models]
    return {
        "campaign_id": campaign_id,
        "registry_hash": registry_hash,
        "models": models,
        "canonical_model_ids": canonical_model_ids,
        "prepared_units": prepared_units,
        "adapter_statuses": adapter_statuses,
        "authority_refs": tuple(authority_refs),
    }


def _iter_campaign_rows_from_inputs(inputs: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    for canonical_model_id, entry in inputs["models"]:
        aliases = _legacy_aliases(entry)
        adapter_status = _adapter_status(
            canonical_model_id,
            inputs["adapter_statuses"],
        )
        for prepared in inputs["prepared_units"]:
            yield _build_row(
                campaign_id=str(inputs["campaign_id"]),
                canonical_model_id=canonical_model_id,
                legacy_aliases=aliases,
                registry_hash=str(inputs["registry_hash"]),
                prepared=prepared,
                adapter_status=adapter_status,
                authority_refs=tuple(inputs["authority_refs"]),
            )


def _model_applicability_counts(
    canonical_model_ids: Iterable[str],
    adapter_statuses: Mapping[str, str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for canonical_model_id in canonical_model_ids:
        status = _adapter_status(canonical_model_id, adapter_statuses)
        key = f"adapter_status:{status}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _instrument_applicability_counts(prepared_units: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    executable = 0
    for prepared in prepared_units:
        blocker_code = str(prepared.get("blocker_code") or "")
        if blocker_code:
            counts[blocker_code] = counts.get(blocker_code, 0) + 1
        else:
            executable += 1
    counts["hbt_executable_prepared_unit"] = executable
    return counts


def _executable_prepared_unit_count(prepared_units: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for prepared in prepared_units if not str(prepared.get("blocker_code") or ""))


def _blocker_prepared_unit_count(prepared_units: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for prepared in prepared_units if str(prepared.get("blocker_code") or ""))


def _validate_required_row_fields(
    row: Mapping[str, Any],
    *,
    required_fields: Iterable[str],
    error_prefix: str,
) -> None:
    missing = [field for field in required_fields if field not in row]
    if missing:
        joined = ",".join(missing)
        raise HftBacktestOnlyCampaignManifestError(
            f"{error_prefix}_missing_required_fields:{joined}"
        )


def _build_parameter_surface_row(
    *,
    campaign_row: Mapping[str, Any],
    parameter_set: Mapping[str, Any],
    parameter_hash: str,
) -> dict[str, Any]:
    authority_refs = _combine_authority_refs(
        campaign_row["authority_refs"],
        parameter_set.get("authority_refs"),
    )
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
        "source_candidate_id": parameter_set.get("source_candidate_id", ""),
        "strategy_params": dict(parameter_set["strategy_params"]),
        "parameter_proposal_status": parameter_set["parameter_proposal_status"],
        "objective_evaluations": parameter_set["objective_evaluations"],
        "optimizer_claim": parameter_set["optimizer_claim"],
        "adapter_status": campaign_row["adapter_status"],
        "admissibility_status": campaign_row["admissibility_status"],
        "blocker_code": campaign_row["blocker_code"],
        "blocker_detail": campaign_row["blocker_detail"],
        "authority_refs": list(authority_refs),
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

    source_hash = _prepared_cached_file_hash(
        prepared,
        key="source_npz_sha256",
        verified_key="_source_npz_sha256_verified",
        path=source_path,
        path_exists=source_exists,
    )
    snapshot_hash = _prepared_cached_file_hash(
        prepared,
        key="initial_snapshot_sha256",
        verified_key="_initial_snapshot_sha256_verified",
        path=snapshot_path,
        path_exists=snapshot_exists,
    )
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


def normalize_self_learning_parameter_sets_payload(payload: Any) -> list[dict[str, Any]]:
    """Validate and normalize the self-learning parameter-set export envelope."""
    if not isinstance(payload, Mapping):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_self_learning_payload_must_be_object"
        )
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != HBT_SELF_LEARNING_PARAMETER_SET_SCHEMA_VERSION:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_self_learning_schema_version"
        )
    source = str(payload.get("source") or "").strip()
    if source != HBT_SELF_LEARNING_PARAMETER_SET_SOURCE:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_self_learning_source"
        )
    raw_parameter_sets = payload.get("parameter_sets")
    if not isinstance(raw_parameter_sets, list):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_self_learning_parameter_sets_must_be_list"
        )
    envelope_status = str(payload.get("parameter_proposal_status") or "").strip()
    if envelope_status != _PRE_HBT_PROPOSAL_STATUS:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_self_learning_proposal_status"
        )
    envelope_objective_evaluations = payload.get("objective_evaluations")
    if not isinstance(envelope_objective_evaluations, int) or isinstance(
        envelope_objective_evaluations,
        bool,
    ):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
        )
    if envelope_objective_evaluations != 0:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_pre_hbt_objective_evaluations_must_be_zero"
        )
    if payload.get("optimizer_claim") is not False:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_self_learning_optimizer_claim_must_be_false"
        )
    declared_parameter_set_count = payload.get("parameter_set_count")
    if not isinstance(declared_parameter_set_count, int) or isinstance(
        declared_parameter_set_count,
        bool,
    ):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_self_learning_parameter_set_count_mismatch"
        )
    if declared_parameter_set_count != len(raw_parameter_sets):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_self_learning_parameter_set_count_mismatch"
        )
    envelope_authority_refs = _authority_ref_tuple(payload.get("authority_refs") or ())
    missing_envelope_refs = _missing_self_learning_authority_refs(envelope_authority_refs)
    if missing_envelope_refs:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_missing_self_learning_authority_refs:"
            + ",".join(missing_envelope_refs)
        )
    out: list[dict[str, Any]] = []
    for raw_spec in raw_parameter_sets:
        if not isinstance(raw_spec, Mapping):
            raise HftBacktestOnlyCampaignManifestError(
                "parameter_surface_self_learning_parameter_set_must_be_object"
            )
        spec = dict(raw_spec)
        spec.setdefault("schema_version", schema_version)
        spec.setdefault("source", source)
        spec["authority_refs"] = list(
            dict.fromkeys(
                [
                    *envelope_authority_refs,
                    *_authority_ref_tuple(spec.get("authority_refs") or ()),
                ]
            )
        )
        out.append(_normalize_parameter_set(spec))
    return out


def _normalize_parameter_set(spec: Mapping[str, Any]) -> dict[str, Any]:
    schema_version = str(spec.get("schema_version") or "").strip()
    if schema_version != HBT_SELF_LEARNING_PARAMETER_SET_SCHEMA_VERSION:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_self_learning_schema_version"
        )
    source = str(spec.get("source") or "").strip()
    if source != HBT_SELF_LEARNING_PARAMETER_SET_SOURCE:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_invalid_self_learning_source"
        )
    authority_refs = _authority_ref_tuple(spec.get("authority_refs") or ())
    missing_refs = _missing_self_learning_authority_refs(authority_refs)
    if missing_refs:
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_missing_self_learning_authority_refs:"
            + ",".join(missing_refs)
        )
    canonical_model_id = str(spec.get("canonical_model_id") or "").strip()
    if canonical_model_id and _LEGACY_MODEL_ID_RE.match(canonical_model_id):
        raise HftBacktestOnlyCampaignManifestError(
            f"parameter_surface_legacy_model_id_as_active:{canonical_model_id}"
        )
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
    source_candidate_id = str(spec.get("source_candidate_id") or "").strip()
    return {
        "schema_version": schema_version,
        "source": source,
        "canonical_model_id": canonical_model_id,
        "parameter_family": parameter_family,
        "source_candidate_id": source_candidate_id,
        "strategy_params": dict(strategy_params),
        "parameter_proposal_status": status,
        "objective_evaluations": objective_evaluations,
        "optimizer_claim": optimizer_claim,
        "authority_refs": list(authority_refs),
    }


def _missing_self_learning_authority_refs(authority_refs: Iterable[str]) -> list[str]:
    refs = set(authority_refs)
    return [ref for ref in HBT_SELF_LEARNING_PARAMETER_SET_AUTHORITY_REFS if ref not in refs]


def _normalize_parameter_sets(
    parameter_sets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [_normalize_parameter_set(spec) for spec in parameter_sets]


def _parameter_set_applies_to_row(
    parameter_set: Mapping[str, Any],
    campaign_row: Mapping[str, Any],
) -> bool:
    canonical_model_id = str(parameter_set.get("canonical_model_id") or "")
    return not canonical_model_id or canonical_model_id == str(campaign_row["canonical_model_id"])


def _raise_if_missing_executable_parameter_set(
    campaign_row: Mapping[str, Any],
    *,
    applied: bool,
) -> None:
    if applied:
        return
    if (
        str(campaign_row.get("admissibility_status") or "") == "admissible"
        and str(campaign_row.get("adapter_status") or "") in READY_ADAPTER_STATUSES
    ):
        raise HftBacktestOnlyCampaignManifestError(
            "parameter_surface_missing_for_executable_model:"
            f"{campaign_row.get('canonical_model_id')}"
        )


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


def _parameter_hash(
    *,
    parameter_family: str,
    strategy_params: Mapping[str, Any],
    canonical_model_id: str = "",
) -> str:
    payload = {
        "parameter_family": parameter_family,
        "strategy_params": dict(strategy_params),
    }
    if canonical_model_id:
        payload["canonical_model_id"] = canonical_model_id
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


def _prepared_cached_file_hash(
    prepared: Mapping[str, Any],
    *,
    key: str,
    verified_key: str,
    path: Path | None,
    path_exists: bool,
) -> str:
    if isinstance(prepared, dict) and prepared.get(verified_key):
        return str(prepared.get(key) or "")
    if path_exists and path is not None:
        file_hash = _sha256_file(path)
    else:
        file_hash = str(prepared.get(key) or "")
    if isinstance(prepared, dict):
        prepared[key] = file_hash
        prepared[verified_key] = True
    return file_hash


def _path_with_added_suffix(path: Path, suffix: str) -> Path:
    return Path(path).with_name(Path(path).name + suffix)


def _cleanup_partial_outputs(tmp_path: Path, checkpoint_path: Path | None) -> None:
    Path(tmp_path).unlink(missing_ok=True)
    if checkpoint_path is not None:
        Path(checkpoint_path).unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _path_with_added_suffix(path, ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


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
