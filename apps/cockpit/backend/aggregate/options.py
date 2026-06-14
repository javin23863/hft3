"""Options zone - first-class read-only CME options lane status.

The lane spec allows CME options research/backtest in Phases 0-1; only
shadow/live execution is blocked while the lane-scoped defect ledger is open.
This zone reuses the System options readiness primitives so the cockpit has one
source of truth and no new pipeline surface.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths, schemas
from . import system

_BLOCKED_STATUSES = {schemas.FAIL, schemas.MISSING, schemas.STALE, schemas.UNKNOWN}
_CME_OPTIONS_CAMPAIGN_MODES = {"cme_options"}
_LEGACY_OPTIONS_CAMPAIGN_MODES = {"options_lane"}
_LEGACY_OPTIONS_MODEL_IDS = {"DEALER_HEDGING", "PDF_MODEL_5"}
_LEGACY_OPTIONS_PREFIXES = ("OPTIONS_", "PARITY_")
_SUMMARY_TIME_FIELDS = (
    "generated_utc",
    "run_utc",
    "created_utc",
    "completed_utc",
    "as_of_utc",
)
_CAMPAIGN_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
_ROBUSTNESS_PASS_STATUSES = {"clear", "green", "ok", "pass", "passed"}
_CONTEXT_MEASURED_STATUSES = {
    "available",
    "covered",
    "measured",
    "ok",
    "pass",
    "passed",
    "present",
    "valid",
}
_CONTEXT_NOT_MEASURED_STATUSES = {
    "",
    "absent",
    "false",
    "missing",
    "none",
    "not_measured",
    "unknown",
    "unmeasured",
}
_CONTEXT_COUNT_KEYS = (
    "options_context_feature_count",
    "options_context_features_count",
    "n_options_context_features",
    "n_events_with_options_context",
    "events_with_options_context",
    "options_context_event_count",
    "measured_count",
    "covered_events",
    "n_events",
    "count",
)
_CONTEXT_ABLATION_ROW_KEYS = (
    "rows",
    "ablation_rows",
    "context_ablation_rows",
    "measurements",
    "results",
    "cells",
)
_CONTEXT_ABLATION_SECTION_KEYS = (
    "baseline",
    "target_only",
    "target_plus_context",
    "target_plus_options",
    "options_context_features",
    "full_context",
)
_CONTEXT_UNIT_KEYS = ("units", "unit", "feature_units")
_CONTEXT_MISSINGNESS_KEYS = (
    "missing_policy",
    "missingness",
    "missingness_policy",
    "missing_fields",
)
_CONTEXT_TIMESTAMP_SOURCE_KEYS = (
    "source_timestamp_utc",
    "context_event_timestamp_utc",
    "context_timestamp_utc",
    "event_timestamp_utc",
)
_CONTEXT_TIMESTAMP_AVAILABLE_KEYS = (
    "feature_available_utc",
    "feature_availability_utc",
    "available_utc",
    "availability_utc",
)
_CONTEXT_TIMESTAMP_TARGET_KEYS = (
    "target_decision_timestamp_utc",
    "target_decision_utc",
    "decision_timestamp_utc",
    "target_timestamp_utc",
)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.REPO).as_posix()
    except ValueError:
        return str(path)


def _workbench_roots() -> list[Path]:
    candidates = [
        paths.REPO / "artifacts" / "research_cards" / "workbench_runs",
        paths.REPO / "artifacts" / "workbench_runs",
        paths.REPO / "research_cards" / "workbench_runs",
    ]
    env = os.environ.get("HFT3_ARTIFACTS_ROOT")
    if env:
        candidates.append(Path(env).resolve() / "workbench_runs")
    seen: set[Path] = set()
    roots: list[Path] = []
    for root in candidates:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in seen and root.is_dir():
            seen.add(resolved)
            roots.append(root)
    return roots


def _period_names(summary: dict[str, Any]) -> list[str]:
    periods = summary.get("periods")
    if not isinstance(periods, list):
        return []
    names: list[str] = []
    for period in periods:
        if isinstance(period, dict) and period.get("name") is not None:
            names.append(str(period["name"]))
    return names


def _summary_campaign_mode(summary: dict[str, Any]) -> str:
    return str(summary.get("campaign_mode") or "").lower()


def _summary_lane(summary: dict[str, Any]) -> str:
    return str(summary.get("lane") or "").lower()


def _is_cme_options_summary(summary: dict[str, Any]) -> bool:
    model_id = str(summary.get("model_id") or "").upper()
    return (
        model_id.startswith("FOPT_")
        or _summary_lane(summary) in _CME_OPTIONS_CAMPAIGN_MODES
        or _summary_campaign_mode(summary) in _CME_OPTIONS_CAMPAIGN_MODES
    )


def _is_legacy_options_summary(summary: dict[str, Any]) -> bool:
    if _is_cme_options_summary(summary):
        return False
    model_id = str(summary.get("model_id") or "").upper()
    if (
        model_id.startswith(_LEGACY_OPTIONS_PREFIXES)
        or model_id in _LEGACY_OPTIONS_MODEL_IDS
    ):
        return True
    if _summary_campaign_mode(summary) in _LEGACY_OPTIONS_CAMPAIGN_MODES:
        return True
    return any("options fixture" in name.lower() for name in _period_names(summary))


def _has_fixture_evidence(summary: dict[str, Any]) -> bool:
    if summary.get("fixture") or summary.get("fixture_backed") is True:
        return True
    if str(summary.get("campaign_mode") or "").lower() == "options_lane":
        return True
    return any("options fixture" in name.lower() for name in _period_names(summary))


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _campaign_timestamp(summary: dict[str, Any]) -> datetime | None:
    match = _CAMPAIGN_TS_RE.search(str(summary.get("campaign_id") or ""))
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _artifact_time(summary_path: Path, summary: dict[str, Any]) -> tuple[float, str | None, str, bool]:
    for field in _SUMMARY_TIME_FIELDS:
        parsed = _parse_utc_datetime(summary.get(field))
        if parsed is not None:
            return parsed.timestamp(), parsed.isoformat(), field, True
    parsed = _campaign_timestamp(summary)
    if parsed is not None:
        return parsed.timestamp(), parsed.isoformat(), "campaign_id", True
    try:
        mtime = summary_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return mtime, paths.mtime_iso(summary_path), "mtime", False


def _float_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _summary_trade_count(summary: dict[str, Any]) -> float:
    top_level = _float_value(summary.get("num_trades"))
    periods = summary.get("periods")
    period_total = 0.0
    if isinstance(periods, list):
        for period in periods:
            if isinstance(period, dict):
                period_total += _float_value(period.get("num_trades"))
    return top_level if top_level > 0 else period_total


def _extra(summary: dict[str, Any]) -> dict[str, Any]:
    extra = summary.get("extra")
    return extra if isinstance(extra, dict) else {}


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _nonempty_container(value: Any) -> bool:
    return isinstance(value, (dict, list, tuple, set)) and bool(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _coverage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    coverage = summary.get("coverage_summary")
    return coverage if isinstance(coverage, dict) else {}


def _has_source_ids(summary: dict[str, Any]) -> bool:
    coverage = _coverage_summary(summary)
    return (
        _nonempty_list(summary.get("source_ids"))
        or _nonempty_list(summary.get("data_source_ids"))
        or _nonempty_list(coverage.get("source_ids"))
    )


def _has_timestamp_ids(summary: dict[str, Any]) -> bool:
    coverage = _coverage_summary(summary)
    return _nonempty_container(summary.get("timestamp_ids")) or _nonempty_container(
        coverage.get("timestamp_ids")
    )


def _uses_2026_options_data(summary: dict[str, Any]) -> bool:
    return summary.get("uses_2026_options_data") is True


def _has_2026_usage_class(summary: dict[str, Any]) -> bool:
    return bool(summary.get("options_2026_usage_class") or summary.get("usage_class"))


def _robustness_status(robustness: Any) -> tuple[str, str | None]:
    if not isinstance(robustness, dict):
        return "not_observed", None
    status = str(
        robustness.get("status")
        or robustness.get("gate_status")
        or robustness.get("result")
        or "observed"
    )
    return status, status.lower()


def _real_data_proof_missing(summary: dict[str, Any], robustness: Any) -> list[str]:
    missing: list[str] = []
    if not _has_source_ids(summary):
        missing.append("source_ids")
    if not _has_timestamp_ids(summary):
        missing.append("timestamp_ids")
    if _summary_trade_count(summary) <= 0:
        missing.append("nonzero_num_trades")
    _, normalized_robustness = _robustness_status(robustness)
    if normalized_robustness not in _ROBUSTNESS_PASS_STATUSES:
        missing.append("robustness_pass")
    if _uses_2026_options_data(summary) and not _has_2026_usage_class(summary):
        missing.append("options_2026_usage_class")
    return missing


def _failure_notes(summary: dict[str, Any]) -> list[str]:
    notes = summary.get("failure_notes")
    if isinstance(notes, list):
        return [str(note) for note in notes]
    return []


def _is_structural(summary: dict[str, Any]) -> bool:
    notes = " ".join(_failure_notes(summary)).lower()
    return (
        summary.get("structural_only") is True
        or _extra(summary).get("structural_only") is True
        or "structural-only" in notes
        or "structural only" in notes
    )


def _is_degraded(summary: dict[str, Any]) -> bool:
    return (
        summary.get("degraded") is True
        or bool(_failure_notes(summary))
        or _promotable(summary) is False
    )


def _promotable(summary: dict[str, Any]) -> Any:
    if "promotable" in summary:
        return summary.get("promotable")
    extra = _extra(summary)
    if "promotable" in extra:
        return extra.get("promotable")
    return None


def _latest_options_summary(predicate) -> tuple[Path, dict[str, Any]] | None:
    latest: tuple[tuple[int, float, float], Path, dict[str, Any]] | None = None
    for root in _workbench_roots():
        try:
            summaries = root.glob("*/summary.json")
        except OSError:
            continue
        for path in summaries:
            data = paths.read_json(path)
            if not isinstance(data, dict) or not predicate(data):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            artifact_epoch, _, _, has_semantic_time = _artifact_time(path, data)
            key = (1 if has_semantic_time else 0, artifact_epoch, mtime)
            if latest is None or key > latest[0]:
                latest = (key, path, data)
    if latest is None:
        return None
    return latest[1], latest[2]


def _summary_evidence_update(summary_path: Path, summary: dict[str, Any]) -> dict:
    robustness_path = summary_path.parent / "robustness_summary.json"
    robustness = paths.read_json(robustness_path)
    claimed_real_data_backed = summary.get("real_data_backed") is True
    missing_real_data_proof = _real_data_proof_missing(summary, robustness)
    real_data_proof_passed = claimed_real_data_backed and not missing_real_data_proof
    fixture_backed = _has_fixture_evidence(summary)
    structural_only = _is_structural(summary)
    degraded = _is_degraded(summary)
    if structural_only:
        status = "structural_only"
    elif degraded:
        status = "artifact_degraded"
    elif real_data_proof_passed:
        status = "real_data_backed"
    elif claimed_real_data_backed:
        status = "real_data_claim_unverified"
    elif fixture_backed:
        status = "fixture_only"
    else:
        status = "artifact_present_unclassified"
    real_data_backed = status == "real_data_backed"
    robustness_status, _ = _robustness_status(robustness)
    robustness_artifact = None
    if isinstance(robustness, dict):
        robustness_artifact = _rel(robustness_path)
    artifact_epoch, artifact_time_utc, artifact_time_source, _ = _artifact_time(summary_path, summary)

    return {
        "status": status,
        "latest_artifact": _rel(summary_path),
        "latest_artifact_status": "present",
        "latest_artifact_mtime_utc": paths.mtime_iso(summary_path),
        "latest_artifact_time_utc": artifact_time_utc,
        "latest_artifact_time_source": artifact_time_source,
        "latest_artifact_time_epoch": artifact_epoch,
        "latest_campaign_id": summary.get("campaign_id") or summary_path.parent.name,
        "latest_model_id": summary.get("model_id"),
        "latest_symbol": summary.get("symbol"),
        "latest_summary_status": summary.get("status"),
        "latest_campaign_mode": summary.get("campaign_mode"),
        "latest_lane": summary.get("lane"),
        "real_data_backed": real_data_backed,
        "claimed_real_data_backed": claimed_real_data_backed,
        "missing_real_data_proof": missing_real_data_proof,
        "fixture_backed": fixture_backed,
        "structural_only": structural_only,
        "degraded": degraded,
        "failure_notes": _failure_notes(summary),
        "promotable": _promotable(summary),
        "trade_count": _summary_trade_count(summary),
        "robustness_status": robustness_status,
        "robustness_artifact": robustness_artifact,
    }


def _standalone_model_evidence() -> dict:
    base = {
        "status": "structural_only",
        "lane": "cme_options",
        "model_id_prefix": "FOPT_",
        "latest_artifact": None,
        "latest_artifact_status": "missing",
        "real_data_backed": False,
        "fixture_backed": False,
        "structural_only": True,
        "robustness_status": "not_observed",
        "robustness_detail": (
            "CMEOptionsBacktester returns structural evidence only; no real-data "
            "FOPT robustness artifact was observed."
        ),
        "next_required_artifact": "artifacts/research_cards/workbench_runs/<run_id>/summary.json",
        "fixture_contract_path": "tests/test_workbench/test_options_lane_campaign.py",
        "authority_sources": [
            "packages/hft3/validation/lanes/adapters/cme_options_adapter.py",
            "packages/hft3/validation/lanes/registration.py",
            "tests/test_workbench/test_options_lane_campaign.py",
        ],
    }
    latest = _latest_options_summary(_is_cme_options_summary)
    if latest is None:
        return base

    summary_path, summary = latest
    update = _summary_evidence_update(summary_path, summary)
    if update["real_data_backed"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is real-data-backed with source IDs, "
            "timestamp IDs, nonzero trades, and passing robustness evidence."
        )
    elif update["structural_only"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is structural-only or degraded; it is not "
            "evidence for a tradable standalone options model."
        )
    elif update["status"] == "artifact_degraded":
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is degraded or non-promotable and cannot "
            "be promoted as real-data evidence."
        )
    elif update["status"] == "real_data_claim_unverified":
        missing = ", ".join(update["missing_real_data_proof"])
        update["robustness_detail"] = (
            "Latest FOPT workbench summary claims real-data backing but is missing "
            f"required proof: {missing}."
        )
    elif update["fixture_backed"]:
        update["robustness_detail"] = (
            "Latest FOPT workbench summary is fixture-backed; no real-data FOPT "
            "robustness artifact was observed."
        )
    else:
        update["robustness_detail"] = (
            "Latest FOPT summary was observed but does not identify fixture or "
            "real-data backing."
        )
    base.update(update)
    return base


def _legacy_options_fixture_evidence() -> dict:
    base = {
        "status": "missing",
        "lane": "legacy_options_parity",
        "model_id_prefix": "OPTIONS_/PARITY_",
        "latest_artifact": None,
        "latest_artifact_status": "missing",
        "real_data_backed": False,
        "fixture_backed": False,
        "structural_only": False,
        "robustness_status": "not_observed",
        "robustness_detail": "No legacy options/parity workbench summary was observed.",
        "authority_sources": [
            "packages/hft3/validation/lanes/registration.py",
            "apps/workbench/src/run/campaign_runner.py",
            "tests/test_workbench/test_options_lane_campaign.py",
        ],
    }
    latest = _latest_options_summary(_is_legacy_options_summary)
    if latest is None:
        return base

    summary_path, summary = latest
    update = _summary_evidence_update(summary_path, summary)
    if update["real_data_backed"]:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is real-data-backed, but it is "
            "still not FOPT CME_OPTIONS evidence."
        )
    elif update["structural_only"] or update["status"] == "artifact_degraded":
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is structural-only, degraded, or "
            "non-promotable; it is not FOPT CME_OPTIONS evidence."
        )
    elif update["status"] == "real_data_claim_unverified":
        missing = ", ".join(update["missing_real_data_proof"])
        update["robustness_detail"] = (
            "Latest legacy options/parity summary claims real-data backing but is "
            f"missing required proof: {missing}. It is still not FOPT CME_OPTIONS evidence."
        )
    elif update["fixture_backed"]:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary is fixture-backed; it is not FOPT "
            "CME_OPTIONS evidence."
        )
    else:
        update["robustness_detail"] = (
            "Latest legacy options/parity summary was observed but does not identify "
            "fixture or real-data backing."
        )
    base.update(update)
    return base


def _default_context_feature_coverage() -> dict:
    return {
        "status": "not_measured",
        "options_context_features": "not_measured",
        "options_standalone_strategy": "not_measured",
        "note": "No artifact-level options context-feature coverage is present yet.",
    }


def _context_block(summary: dict[str, Any], key: str) -> Any:
    if key in summary:
        return summary.get(key)
    return _extra(summary).get(key)


def _is_options_context_summary(summary: dict[str, Any]) -> bool:
    return (
        _is_cme_options_summary(summary) or _is_legacy_options_summary(summary)
    ) and (
        _context_block(summary, "context_feature_coverage") is not None
        or _context_block(summary, "context_ablation") is not None
    )


def _context_source_family(summary: dict[str, Any]) -> str:
    if _is_cme_options_summary(summary):
        return "cme_options"
    if _is_legacy_options_summary(summary):
        return "legacy_options_parity"
    return "unknown"


def _first_source_ids(*values: Any) -> list[str]:
    for value in values:
        ids = _string_list(value)
        if ids:
            return ids
    return []


def _row_source_ids(rows: list[Any]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("source_ids", "data_source_ids", "context_source_ids"):
            ids.extend(_string_list(row.get(key)))
    return list(dict.fromkeys(ids))


def _first_timestamp_ids(*values: Any) -> Any:
    for value in values:
        if _nonempty_container(value):
            return value
    return None


def _row_timestamp_ids(rows: list[Any]) -> list[Any]:
    ids: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("timestamp_ids", "context_timestamp_ids"):
            value = row.get(key)
            if _nonempty_container(value):
                ids.append(value)
    return ids


def _first_present_value(*values: Any) -> Any:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
        if _nonempty_container(value):
            return value
    return None


def _row_value(rows: list[Any], keys: tuple[str, ...]) -> Any:
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _first_present_value(*(row.get(key) for key in keys))
        if value is not None:
            return value
    return None


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _positive_number(value: Any) -> float | None:
    number = _nonnegative_number(value)
    if number is None or number <= 0:
        return None
    return number


def _first_context_count(mapping: dict[str, Any]) -> float | None:
    for key in _CONTEXT_COUNT_KEYS:
        count = _positive_number(mapping.get(key))
        if count is not None:
            return count
    return None


def _context_ablation_rows(ablation: Any) -> list[Any]:
    if isinstance(ablation, list):
        return ablation
    if not isinstance(ablation, dict):
        return []
    for key in _CONTEXT_ABLATION_ROW_KEYS:
        rows = ablation.get(key)
        if isinstance(rows, list):
            return rows
    if any(key in ablation for key in _CONTEXT_ABLATION_SECTION_KEYS):
        return [ablation]
    return []


def _timestamp_first_value(timestamp_ids: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(timestamp_ids, dict):
        for key in keys:
            value = timestamp_ids.get(key)
            if value:
                return value
    if isinstance(timestamp_ids, list):
        for item in timestamp_ids:
            value = _timestamp_first_value(item, keys)
            if value:
                return value
    return None


def _timestamp_proof_gaps(timestamp_ids: Any) -> tuple[list[str], list[str]]:
    if timestamp_ids is None:
        return ["timestamp_ids"], []
    return _timestamp_proof_entry_gaps(timestamp_ids, "timestamp_ids")


def _timestamp_proof_entry_gaps(entry: Any, prefix: str) -> tuple[list[str], list[str]]:
    if isinstance(entry, list):
        if not entry:
            return [prefix], []
        missing: list[str] = []
        violations: list[str] = []
        for idx, item in enumerate(entry):
            item_missing, item_violations = _timestamp_proof_entry_gaps(
                item, f"{prefix}[{idx}]"
            )
            missing.extend(item_missing)
            violations.extend(item_violations)
        return missing, violations
    if not isinstance(entry, dict):
        return [prefix], []

    missing: list[str] = []
    violations: list[str] = []
    source_raw = _timestamp_first_value(entry, _CONTEXT_TIMESTAMP_SOURCE_KEYS)
    available_raw = _timestamp_first_value(entry, _CONTEXT_TIMESTAMP_AVAILABLE_KEYS)
    target_raw = _timestamp_first_value(entry, _CONTEXT_TIMESTAMP_TARGET_KEYS)
    if not source_raw:
        missing.append(f"{prefix}.source_timestamp")
    if not available_raw:
        missing.append(f"{prefix}.feature_available")
    if not target_raw:
        missing.append(f"{prefix}.target_decision")

    source = _parse_utc_datetime(source_raw)
    available = _parse_utc_datetime(available_raw)
    target = _parse_utc_datetime(target_raw)
    if source_raw and source is None:
        violations.append(f"{prefix}.source_timestamp_unparseable")
    if available_raw and available is None:
        violations.append(f"{prefix}.feature_available_unparseable")
    if target_raw and target is None:
        violations.append(f"{prefix}.target_decision_unparseable")
    if source is not None and available is not None and source > available:
        violations.append(f"{prefix}.source_after_feature_available")
    if available is not None and target is not None and available > target:
        violations.append(f"{prefix}.feature_available_after_target_decision")
    return missing, violations


def _options_context_feature_measurement(
    coverage: dict[str, Any], rows: list[Any]
) -> tuple[bool, float | None, Any]:
    feature_measurement = coverage.get("options_context_features")
    if isinstance(feature_measurement, dict):
        count = _first_context_count(feature_measurement)
        if count is not None:
            return True, count, feature_measurement
        return False, count, feature_measurement

    count = _first_context_count(coverage)
    if count is not None:
        return True, count, feature_measurement if feature_measurement is not None else count

    if isinstance(feature_measurement, str):
        normalized = feature_measurement.strip().lower()
        if normalized in _CONTEXT_MEASURED_STATUSES | _CONTEXT_NOT_MEASURED_STATUSES:
            return False, None, feature_measurement
    elif feature_measurement is True:
        return False, None, feature_measurement
    else:
        numeric = _positive_number(feature_measurement)
        if numeric is not None:
            return True, numeric, feature_measurement

    for key in ("options_context_feature_measured", "options_context_measured"):
        if coverage.get(key) is True:
            return False, None, coverage.get(key)

    for row in rows:
        if not isinstance(row, dict):
            continue
        row_text = " ".join(
            str(row.get(key) or "")
            for key in ("context_source", "context_set", "feature_group", "feature_id")
        ).lower()
        if "option" not in row_text:
            continue
        row_count = _first_context_count(row)
        if row_count is not None:
            return True, row_count, row
    return False, None, feature_measurement


def _context_feature_coverage_update(summary_path: Path, summary: dict[str, Any]) -> dict:
    coverage_raw = _context_block(summary, "context_feature_coverage")
    ablation_raw = _context_block(summary, "context_ablation")
    malformed_fields: list[str] = []
    if coverage_raw is not None and not isinstance(coverage_raw, dict):
        malformed_fields.append("context_feature_coverage")
    if ablation_raw is not None and not isinstance(ablation_raw, (dict, list)):
        malformed_fields.append("context_ablation")

    coverage = coverage_raw if isinstance(coverage_raw, dict) else {}
    ablation_rows = _context_ablation_rows(ablation_raw)
    source_ids = _first_source_ids(
        coverage.get("source_ids"),
        coverage.get("data_source_ids"),
        coverage.get("context_source_ids"),
        summary.get("source_ids"),
        summary.get("data_source_ids"),
        _coverage_summary(summary).get("source_ids"),
        _row_source_ids(ablation_rows),
    )
    timestamp_ids = _first_timestamp_ids(
        coverage.get("timestamp_ids"),
        coverage.get("context_timestamp_ids"),
        summary.get("timestamp_ids"),
        _coverage_summary(summary).get("timestamp_ids"),
        _row_timestamp_ids(ablation_rows),
    )
    units = _first_present_value(*(coverage.get(key) for key in _CONTEXT_UNIT_KEYS))
    if units is None:
        units = _row_value(ablation_rows, _CONTEXT_UNIT_KEYS)
    missing_policy = _first_present_value(
        *(coverage.get(key) for key in _CONTEXT_MISSINGNESS_KEYS)
    )
    if missing_policy is None:
        missing_policy = _row_value(ablation_rows, _CONTEXT_MISSINGNESS_KEYS)
    feature_measured, feature_count, feature_measurement = _options_context_feature_measurement(
        coverage, ablation_rows
    )
    timestamp_missing_fields, timestamp_violations = _timestamp_proof_gaps(timestamp_ids)
    missing_fields: list[str] = []
    if not source_ids:
        missing_fields.append("source_ids")
    missing_fields.extend(timestamp_missing_fields)
    if not ablation_rows:
        missing_fields.append("context_ablation")
    if not feature_measured:
        missing_fields.append("options_context_features")
    if units is None:
        missing_fields.append("units")
    if missing_policy is None:
        missing_fields.append("missing_policy")

    if malformed_fields:
        status = "malformed"
        feature_status = "malformed"
        note = "Options context artifact block is malformed; coverage is fail-closed."
    elif timestamp_violations:
        status = "malformed"
        feature_status = "malformed"
        note = "Options context artifact violates timestamp proof; coverage is fail-closed."
    elif missing_fields:
        status = "incomplete"
        feature_status = "incomplete"
        note = "Options context artifact is missing required proof; coverage is fail-closed."
    else:
        status = "measured"
        feature_status = "measured"
        note = "Latest options-family workbench summary includes artifact-backed context coverage."
    artifact_epoch, artifact_time_utc, artifact_time_source, _ = _artifact_time(summary_path, summary)
    return {
        "status": status,
        "options_context_features": feature_status,
        "options_context_feature_measured": status == "measured",
        "options_context_feature_count": feature_count,
        "options_context_feature_measurement": feature_measurement,
        "options_standalone_strategy": coverage.get(
            "options_standalone_strategy",
            coverage.get("standalone_strategy", "separate_field"),
        ),
        "standalone_strategy_separated": True,
        "standalone_evidence_field": "standalone_model_evidence",
        "latest_artifact": _rel(summary_path),
        "latest_artifact_status": "present",
        "latest_artifact_mtime_utc": paths.mtime_iso(summary_path),
        "latest_artifact_time_utc": artifact_time_utc,
        "latest_artifact_time_source": artifact_time_source,
        "latest_artifact_time_epoch": artifact_epoch,
        "latest_campaign_id": summary.get("campaign_id") or summary_path.parent.name,
        "latest_model_id": summary.get("model_id"),
        "latest_symbol": summary.get("symbol"),
        "latest_summary_status": summary.get("status"),
        "latest_campaign_mode": summary.get("campaign_mode"),
        "latest_lane": summary.get("lane"),
        "source_family": _context_source_family(summary),
        "source_ids": source_ids,
        "timestamp_ids": timestamp_ids,
        "units": units,
        "missing_policy": missing_policy,
        "context_ablation_row_count": len(ablation_rows),
        "context_ablation_rows": ablation_rows,
        "missing_fields": missing_fields,
        "malformed_fields": malformed_fields + timestamp_violations,
        "note": note,
    }


def _context_feature_coverage() -> dict:
    latest = _latest_options_summary(_is_options_context_summary)
    if latest is None:
        return _default_context_feature_coverage()
    summary_path, summary = latest
    return _context_feature_coverage_update(summary_path, summary)


def _health(data_status: str, defect_status: str, *, shadow_live_blocked: bool) -> str:
    if data_status == schemas.FAIL:
        return schemas.RED
    if (
        data_status in {schemas.MISSING, schemas.STALE, schemas.UNKNOWN}
        or defect_status == schemas.FAIL
        or shadow_live_blocked
    ):
        return schemas.AMBER
    return schemas.GREEN


def build() -> dict:
    data = system._options_data_readiness()
    defects = system._options_defect_ledger()
    data_status = str(data.get("status", schemas.UNKNOWN))
    defect_status = str(defects.get("status", schemas.UNKNOWN))
    context_feature_coverage = _context_feature_coverage()
    research_only = True
    shadow_live_blocked = True
    blocked_reasons = ["shadow_live_phase_gate"]
    if data_status in _BLOCKED_STATUSES:
        blocked_reasons.append(f"data_readiness:{data_status}")
    if defect_status == schemas.FAIL:
        blocked_reasons.append("defect_ledger_open")
    health = _health(data_status, defect_status, shadow_live_blocked=shadow_live_blocked)
    if context_feature_coverage.get("status") == "malformed":
        health = schemas.RED
    elif context_feature_coverage.get("status") == "incomplete" and health == schemas.GREEN:
        health = schemas.AMBER
    return {
        "zone": "options",
        "generated_utc": paths.now_iso(),
        "health": health,
        "lane": "cme_options",
        "model_id_prefix": "FOPT_",
        "phase": "research_backtest_only",
        "research_backtest_status": "allowed",
        "research_backtest_detail": (
            "OPTIONS_LANE.md Phases 0-1 allow research/backtest; shadow/live "
            "execution remains blocked until the options gates clear."
        ),
        "execution_status": "shadow_live_blocked",
        "research_only": research_only,
        "data_readiness": data,
        "defect_ledger": defects,
        "context_feature_coverage": context_feature_coverage,
        "standalone_model_evidence": _standalone_model_evidence(),
        "legacy_options_fixture_evidence": _legacy_options_fixture_evidence(),
        "shadow_live_status": "blocked",
        "shadow_live_blockers": blocked_reasons,
        "controls": {
            "live_order_controls": False,
            "paper_order_controls": False,
            "reason": (
                "Options research/backtest is allowed; live/paper order controls "
                "remain disabled until Phase 2/3 gates clear."
            ),
        },
        "authority_sources": [
            "specs/OPTIONS_LANE.md",
            "vault:decisions/2026-06-12 Options-lane build decisions (slices 1-7).md",
            "vault:sessions/2026-06-13 Options backfill, study verdicts, cockpit integration.md",
        ],
    }
