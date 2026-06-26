"""Continuous CME feature store scaffold (Phase 3).

Builds weekly feature-matrix shells with PIT taxonomy metadata. Scoring and
NPZ ingestion are deferred; leakage guards run on declared timestamps only.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

STORE_SCHEMA_VERSION = "1"

FEATURE_GROUP_KEYS = (
    "order_flow",
    "queue_dynamics",
    "spread_depth",
    "cross_market",
    "calendar_curve",
    "seasonal_state",
    "toxicity",
    "execution_cost",
)

FORBIDDEN_FEATURE_SUFFIXES = (
    "_future_bar",
    "_future_fill",
    "_post_event_label",
    "_later_session_agg",
)

_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def feature_store_dir(repo_root: Path, rithmic_week: str) -> Path:
    safe_week = rithmic_week.replace("-", "_")
    return repo_root / "runtime" / "continuous_cme" / "feature_store" / safe_week


def feature_store_path(repo_root: Path, rithmic_week: str) -> Path:
    return feature_store_dir(repo_root, rithmic_week) / "feature_matrix.json"


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


def empty_feature_group_shell(group_id: str) -> dict[str, Any]:
    """Per-group metadata shell (Phase 3 acceptance shape)."""
    if group_id not in FEATURE_GROUP_KEYS:
        raise ValueError(f"unknown feature group: {group_id!r}")
    return {
        "group_id": group_id,
        "feature_names": [],
        "row_count": 0,
        "missingness_ratio": None,
        "pit_proof": "pending",
    }


def _to_utc(dt: datetime) -> datetime:
    """Normalize naive or aware timestamps to UTC for ordered compare."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def assert_no_timestamp_leakage(
    *,
    decision_timestamp: str,
    source_timestamps: Sequence[str | None],
) -> None:
    """Fail closed when any source timestamp is after the decision clock."""
    decision = _parse_ts(decision_timestamp)
    if decision is None:
        raise ValueError(f"invalid decision_timestamp: {decision_timestamp!r}")
    decision_utc = _to_utc(decision)
    for index, raw in enumerate(source_timestamps):
        if raw is None:
            continue
        source = _parse_ts(raw)
        if source is None:
            raise ValueError(f"invalid source_timestamp at index {index}: {raw!r}")
        if _to_utc(source) > decision_utc:
            raise ValueError(
                f"timestamp_leakage: source[{index}]={raw!r} > decision={decision_timestamp!r}"
            )


def assert_no_forbidden_feature_names(feature_names: Sequence[str]) -> None:
    """Reject feature keys that encode lookahead by naming convention."""
    for name in feature_names:
        lowered = str(name).lower()
        for suffix in FORBIDDEN_FEATURE_SUFFIXES:
            if lowered.endswith(suffix):
                raise ValueError(f"forbidden_lookahead_feature:{name}")


def validate_feature_group_missingness(group: Mapping[str, Any]) -> list[str]:
    """Return missingness validation errors for one feature-group shell."""
    errors: list[str] = []
    row_count = group.get("row_count", 0)
    if type(row_count) is not int or row_count < 0:
        errors.append("invalid_row_count")
        return errors
    missingness = group.get("missingness_ratio")
    if row_count == 0:
        return errors
    if missingness is None:
        errors.append("missing_missingness_ratio")
        return errors
    if isinstance(missingness, bool) or not isinstance(missingness, (int, float)):
        errors.append("invalid_missingness_ratio")
        return errors
    ratio = float(missingness)
    if not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
        errors.append("missingness_out_of_range")
    return errors


def validate_feature_row_pit(row: Mapping[str, Any]) -> list[str]:
    """Return PIT validation errors for one feature-matrix row shell."""
    errors: list[str] = []
    decision = row.get("decision_timestamp")
    if not isinstance(decision, str) or not decision.strip():
        errors.append("missing_decision_timestamp")
        return errors
    if _parse_ts(decision) is None:
        errors.append("invalid_decision_timestamp")
        return errors

    feature_names = row.get("feature_names") or []
    if not isinstance(feature_names, list):
        errors.append("invalid_feature_names")
    else:
        try:
            assert_no_forbidden_feature_names(feature_names)
        except ValueError as exc:
            errors.append(str(exc))

    source_timestamps = row.get("source_timestamps") or []
    if not isinstance(source_timestamps, list):
        errors.append("invalid_source_timestamps")
        return errors
    try:
        assert_no_timestamp_leakage(
            decision_timestamp=decision,
            source_timestamps=[str(v) if v is not None else None for v in source_timestamps],
        )
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def validate_feature_matrix_pit(matrix: Mapping[str, Any]) -> list[str]:
    """Validate feature groups and row shells for PIT constraints."""
    errors: list[str] = []
    groups = matrix.get("feature_groups") or []
    if not isinstance(groups, list):
        errors.append("invalid_feature_groups")
    else:
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                errors.append(f"invalid_group:{index}")
                continue
            feature_names = group.get("feature_names") or []
            if not isinstance(feature_names, list):
                errors.append(f"group[{index}]:invalid_feature_names")
                continue
            try:
                assert_no_forbidden_feature_names(feature_names)
            except ValueError as exc:
                errors.append(f"group[{index}]:{exc}")
            for err in validate_feature_group_missingness(group):
                errors.append(f"group[{index}]:{err}")

    rows = matrix.get("rows") or []
    if not isinstance(rows, list):
        errors.append("invalid_rows")
        return errors
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"invalid_row:{index}")
            continue
        for err in validate_feature_row_pit(row):
            errors.append(f"row[{index}]:{err}")
    return errors


def build_continuous_feature_store_stub(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
    relationship_graph_path: Path | None = None,
) -> dict[str, Any]:
    """Build Phase 3 feature store shell (no weekly NPZ load yet)."""
    graph_ref = None
    if relationship_graph_path is not None:
        graph_ref = str(relationship_graph_path)
    elif (repo_root / "runtime" / "continuous_cme" / "relationship_graph").is_dir():
        candidate = feature_store_dir(repo_root, rithmic_week).parent.parent / (
            "relationship_graph" / rithmic_week.replace("-", "_") / "graph.json"
        )
        if candidate.is_file():
            graph_ref = str(candidate)

    groups = [empty_feature_group_shell(group_id) for group_id in FEATURE_GROUP_KEYS]
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "lane": "continuous",
        "rithmic_week": rithmic_week,
        "universe_profile": universe_profile,
        "relationship_graph_path": graph_ref,
        "feature_groups": groups,
        "rows": [],
        "summary": {
            "group_count": len(groups),
            "row_count": 0,
            "pit_validated": False,
            "data_loaded": False,
        },
    }


def write_continuous_feature_store(repo_root: Path, matrix: dict[str, Any]) -> Path:
    week = str(matrix["rithmic_week"])
    pit_errors = validate_feature_matrix_pit(matrix)
    if pit_errors:
        raise ValueError(f"feature_matrix_pit_invalid:{','.join(pit_errors)}")
    summary = matrix.setdefault("summary", {})
    if isinstance(summary, dict):
        summary["pit_validated"] = True
    path = feature_store_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
