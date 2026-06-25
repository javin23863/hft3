"""Read-only RL campaign budget planning.

The planner accepts caller-supplied feature-manifest rows and budget inputs,
then returns a manifest-like planning receipt. It does not open feature-store
payloads, NPZ files, or any training entrypoint.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import math
from typing import Any

RL_CAMPAIGN_BUDGET_SCHEMA_VERSION = "hft3_rl_campaign_budget_plan_v1"

_ROW_LIKE_KEYS = frozenset(
    {
        "symbol",
        "instrument",
        "event_id",
        "row_count",
        "source_rows",
        "feature_names",
        "features",
    }
)
_SYMBOL_FIELDS = ("symbol", "instrument", "root_symbol", "ticker")
_EVENT_FIELDS = ("event_id", "event", "event_name")
_SOURCE_ROW_COUNT_FIELDS = ("source_rows", "source_row_count", "n_rows")
_GENERIC_ROW_COUNT_FIELDS = ("row_count", "rows")
_BUILT_ROW_COUNT_FIELDS = ("built_rows",)
_FEATURE_FIELDS = ("feature_names", "features", "supported_features", "feature_list")
_PATH_FIELDS = ("path", "store_path", "npz_path", "feature_store_path")
_HASH_FIELDS = ("content_hash", "manifest_hash", "store_sha256", "sha256")
_FEATURE_INDEX_HASH_FIELDS = ("feature_index_hash",)


def plan_rl_campaign_budget(
    *,
    feature_manifest_rows: Iterable[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]],
    vast_credit_usd: float,
    vast_gpu_hour_rate_usd: float,
    budget_reserve_usd: float,
    supported_features: Sequence[str],
    required_features: Sequence[str],
    measured_throughput_rows_per_gpu_hour: float | None = None,
    measured_throughput_row_basis: str = "manifest_source_rows",
    pilot_target_rows: int | None = None,
) -> dict[str, Any]:
    """Build a read-only RL campaign budget plan.

    ``feature_manifest_rows`` must be an already-loaded manifest surface.
    ``measured_throughput_rows_per_gpu_hour`` uses the same row basis as the
    manifest inventory row counts, normally source rows from the feature store
    manifest. This function deliberately treats paths and hashes as opaque
    inventory strings; it never dereferences payload paths or launches training.
    """

    rows = _coerce_manifest_rows(feature_manifest_rows)
    inventory = _inventory_by_symbol(rows)
    pilot_selection = (
        select_stratified_pilot_rows(rows, target_rows=pilot_target_rows)
        if pilot_target_rows is not None
        else None
    )
    supported = _clean_unique_strings(supported_features, "supported_features")
    required = _clean_unique_strings(required_features, "required_features")
    supported_set = set(supported)
    unsupported_required = [feature for feature in required if feature not in supported_set]

    credit = _non_negative_float(vast_credit_usd, "vast_credit_usd")
    rate = _positive_float(vast_gpu_hour_rate_usd, "vast_gpu_hour_rate_usd")
    reserve = _non_negative_float(budget_reserve_usd, "budget_reserve_usd")
    theoretical_gpu_hours = _round_hours(credit / rate)
    usable_credit = max(0.0, credit - reserve)
    usable_gpu_hours = _round_hours(usable_credit / rate)
    throughput = _optional_positive_float(
        measured_throughput_rows_per_gpu_hour,
        "measured_throughput_rows_per_gpu_hour",
    )
    throughput_basis = _clean_string(measured_throughput_row_basis, "measured_throughput_row_basis")
    throughput_comparable = throughput is not None and throughput_basis == "manifest_source_rows"

    known_rows = sum(
        int(summary["total_rows"])
        for summary in inventory.values()
        if summary["total_rows"] is not None
    )
    missing_row_count_entries = sum(
        int(summary["missing_row_count_entries"]) for summary in inventory.values()
    )
    non_source_row_count_entries = sum(
        int(summary["non_source_row_count_entries"]) for summary in inventory.values()
    )
    source_inventory_complete = (
        bool(inventory) and missing_row_count_entries == 0 and non_source_row_count_entries == 0
    )
    manifest_fingerprint = _manifest_source_row_fingerprint(rows)
    estimated_trainable_rows = (
        int(math.floor(usable_gpu_hours * throughput))
        if throughput_comparable and source_inventory_complete
        else None
    )
    estimated_full_inventory_gpu_hours = (
        None
        if not throughput_comparable or not source_inventory_complete or known_rows <= 0
        else _round_hours(known_rows / throughput)
    )
    estimated_full_inventory_cost_usd = (
        None
        if estimated_full_inventory_gpu_hours is None
        else _round_hours(estimated_full_inventory_gpu_hours * rate)
    )
    estimated_full_inventory_covered = (
        None
        if not throughput_comparable or not source_inventory_complete or known_rows <= 0
        else bool(estimated_trainable_rows is not None and estimated_trainable_rows >= known_rows)
    )

    inventory_reasons = [] if inventory else ["feature_manifest_rows_missing"]
    pilot_reasons = list(inventory_reasons)
    if known_rows <= 0:
        pilot_reasons.append("inventory_source_rows_zero")
    if pilot_selection is not None and pilot_selection.get("status") != "planned":
        pilot_reasons.extend(str(reason) for reason in pilot_selection.get("failure_reasons", []))
    if usable_gpu_hours <= 0.0:
        pilot_reasons.append("usable_gpu_budget_exhausted")

    full_reasons = list(inventory_reasons)
    if unsupported_required:
        full_reasons.append("unsupported_required_features")
    if known_rows <= 0:
        full_reasons.append("inventory_source_rows_zero")
    if missing_row_count_entries:
        full_reasons.append("inventory_source_row_counts_missing")
    if non_source_row_count_entries:
        full_reasons.append("inventory_row_count_basis_not_source")
    if throughput is None:
        full_reasons.append("measured_throughput_missing")
    elif throughput_basis != "manifest_source_rows":
        full_reasons.append("measured_throughput_row_basis_mismatch")
    if usable_gpu_hours <= 0.0:
        full_reasons.append("usable_gpu_budget_exhausted")
    if estimated_full_inventory_covered is False:
        full_reasons.append("budget_insufficient_for_full_inventory")

    pilot_status = "planned" if not pilot_reasons else "blocked"
    full_status = "planned" if not full_reasons else "blocked"
    if full_status == "planned":
        status = "full_training_plan_ready"
    elif pilot_status == "planned":
        status = "pilot_plan_ready_full_training_blocked"
    else:
        status = "blocked"

    return {
        "schema_version": RL_CAMPAIGN_BUDGET_SCHEMA_VERSION,
        "process": "rl_campaign_budget_planning",
        "status": status,
        "read_only": True,
        "npz_payloads_read": False,
        "training_started": False,
        "data_inventory_by_symbol": inventory,
        "supported_features": supported,
        "required_features": required,
        "unsupported_required_features": unsupported_required,
        "theoretical_gpu_hours": theoretical_gpu_hours,
        "usable_gpu_hours": usable_gpu_hours,
        "vast_budget": {
            "credit_usd": credit,
            "gpu_hour_rate_usd": rate,
            "reserve_usd": reserve,
            "usable_credit_usd": _round_hours(usable_credit),
        },
        "measured_throughput_row_basis": throughput_basis,
        "measured_throughput_rows_per_gpu_hour": throughput,
        "estimated_trainable_rows": estimated_trainable_rows,
        "known_inventory_rows": known_rows,
        "manifest_source_row_fingerprint": manifest_fingerprint,
        "missing_row_count_entries": missing_row_count_entries,
        "non_source_row_count_entries": non_source_row_count_entries,
        "estimated_full_inventory_gpu_hours": estimated_full_inventory_gpu_hours,
        "estimated_full_inventory_cost_usd": estimated_full_inventory_cost_usd,
        "estimated_full_inventory_covered": estimated_full_inventory_covered,
        "stage_statuses": {
            "data_inventory": {
                "status": "ready" if inventory else "blocked",
                "failure_reasons": inventory_reasons,
                "manifest_rows": len(rows),
            },
            "stratified_pilot": {
                "status": pilot_status,
                "failure_reasons": _dedupe(pilot_reasons),
                "purpose": "measure throughput and feature coverage before full GPU training",
                "selection": pilot_selection,
            },
            "full_training": {
                "status": full_status,
                "failure_reasons": _dedupe(full_reasons),
                "source_inventory_complete": source_inventory_complete,
                "missing_row_count_entries": missing_row_count_entries,
                "non_source_row_count_entries": non_source_row_count_entries,
                "measured_throughput_row_basis": throughput_basis,
                "estimated_trainable_rows": estimated_trainable_rows,
                "estimated_full_inventory_gpu_hours": estimated_full_inventory_gpu_hours,
                "estimated_full_inventory_cost_usd": estimated_full_inventory_cost_usd,
                "estimated_full_inventory_covered": estimated_full_inventory_covered,
            },
            "downstream_validation": {
                "status": "blocked_downstream_validation_required",
                "failure_reasons": [
                    "VectorBT_screening_required",
                    "robustness_evidence_required",
                    "HftBacktest_execution_realism_required",
                ],
            },
        },
        "decision_time_boundary": (
            "planner only; caller supplied feature-manifest rows are inventoried without "
            "reading NPZ payloads or starting RL training"
        ),
    }


def select_stratified_pilot_rows(
    feature_manifest_rows: Iterable[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]],
    *,
    target_rows: int,
) -> dict[str, Any]:
    """Select manifest rows for an all-symbol throughput pilot.

    Selection is deterministic and metadata-only. It balances a row budget
    across symbols first, then fills any remaining target from the largest
    unused rows. File granularity means the selected row total may exceed the
    requested target.
    """

    rows = _coerce_manifest_rows(feature_manifest_rows)
    target = _positive_int(target_rows, "target_rows")
    groups: dict[str, list[dict[str, Any]]] = {}
    missing_row_count_entries = 0
    for idx, row in enumerate(rows):
        row_count = _row_count(row)
        if row_count is None:
            missing_row_count_entries += 1
            continue
        symbol = _first_non_empty(row, _SYMBOL_FIELDS) or "UNKNOWN"
        groups.setdefault(symbol, []).append(_pilot_row(row, idx, symbol, row_count))

    if not groups:
        return {
            "status": "blocked",
            "failure_reasons": ["no_manifest_rows_with_row_counts"],
            "target_rows": target,
            "selected_rows": 0,
            "selected_manifest_rows": [],
            "selected_symbols": [],
            "missing_row_count_entries": missing_row_count_entries,
        }

    for symbol_rows in groups.values():
        symbol_rows.sort(key=lambda row: (-int(row["row_count"]), str(row["event_id"]), str(row["store_path"])))

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str, str]] = set()
    per_symbol_target = int(math.ceil(target / len(groups)))
    per_symbol_rows: dict[str, int] = {}

    for symbol in sorted(groups):
        accumulated = 0
        for row in groups[symbol]:
            if accumulated >= per_symbol_target:
                break
            key = _pilot_key(row)
            selected.append(row)
            selected_keys.add(key)
            accumulated += int(row["row_count"])
        per_symbol_rows[symbol] = accumulated

    total = sum(int(row["row_count"]) for row in selected)
    if total <= 0:
        return {
            "status": "blocked",
            "failure_reasons": ["no_positive_manifest_rows"],
            "target_rows": target,
            "selected_rows": 0,
            "selected_manifest_row_count": len(selected),
            "selected_symbols": sorted(groups),
            "selected_rows_by_symbol": {symbol: per_symbol_rows.get(symbol, 0) for symbol in sorted(groups)},
            "missing_row_count_entries": missing_row_count_entries,
            "selected_manifest_rows": selected,
        }
    if total < target:
        remaining = [
            row
            for symbol in sorted(groups)
            for row in groups[symbol]
            if _pilot_key(row) not in selected_keys
        ]
        remaining.sort(key=lambda row: (-int(row["row_count"]), str(row["symbol"]), str(row["event_id"])))
        for row in remaining:
            if total >= target:
                break
            selected.append(row)
            selected_keys.add(_pilot_key(row))
            total += int(row["row_count"])
            per_symbol_rows[str(row["symbol"])] = per_symbol_rows.get(str(row["symbol"]), 0) + int(row["row_count"])

    selected.sort(key=lambda row: (str(row["symbol"]), -int(row["row_count"]), str(row["event_id"])))
    return {
        "status": "planned",
        "failure_reasons": [],
        "target_rows": target,
        "selected_rows": total,
        "selected_manifest_row_count": len(selected),
        "selected_symbols": sorted(groups),
        "selected_rows_by_symbol": {symbol: per_symbol_rows.get(symbol, 0) for symbol in sorted(groups)},
        "missing_row_count_entries": missing_row_count_entries,
        "selected_manifest_rows": selected,
    }


def _pilot_row(row: Mapping[str, Any], idx: int, symbol: str, row_count: int) -> dict[str, Any]:
    return {
        "manifest_index": idx,
        "symbol": symbol,
        "event_id": _first_non_empty(row, _EVENT_FIELDS),
        "row_count": int(row_count),
        "store_path": _first_non_empty(row, _PATH_FIELDS),
        "content_hash": _first_non_empty(row, ("content_hash", "manifest_hash", "store_sha256", "sha256")),
    }


def _pilot_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("symbol") or ""), str(row.get("event_id") or ""), str(row.get("store_path") or ""))


def _coerce_manifest_rows(
    feature_manifest_rows: Iterable[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(feature_manifest_rows, Mapping):
        if not feature_manifest_rows:
            return []
        if _looks_like_row_mapping(feature_manifest_rows):
            return [dict(feature_manifest_rows)]
        rows = []
        for key, value in feature_manifest_rows.items():
            if not isinstance(value, Mapping):
                raise ValueError("feature_manifest_rows mapping values must be row objects")
            row = dict(value)
            if isinstance(key, tuple):
                if len(key) >= 1 and not row.get("symbol"):
                    row["symbol"] = key[0]
                if len(key) >= 2 and not row.get("event_id"):
                    row["event_id"] = key[1]
            else:
                row.setdefault("manifest_key", str(key))
            rows.append(row)
        return rows
    if isinstance(feature_manifest_rows, (str, bytes)):
        raise ValueError("feature_manifest_rows must be row objects, not a string path")
    rows = []
    for row in feature_manifest_rows:
        if not isinstance(row, Mapping):
            raise ValueError("feature_manifest_rows must contain row objects")
        rows.append(dict(row))
    return rows


def _looks_like_row_mapping(value: Mapping[Any, Any]) -> bool:
    return any(str(key) in _ROW_LIKE_KEYS for key in value)


def _inventory_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _first_non_empty(row, _SYMBOL_FIELDS) or "UNKNOWN"
        summary = inventory.setdefault(
            symbol,
            {
                "manifest_row_count": 0,
                "event_ids": [],
                "total_rows": 0,
                "missing_row_count_entries": 0,
                "non_source_row_count_entries": 0,
                "row_count_basis_counts": {},
                "features": [],
                "paths": [],
                "hashes": [],
            },
        )
        summary["manifest_row_count"] += 1
        event_id = _first_non_empty(row, _EVENT_FIELDS)
        if event_id:
            summary["event_ids"].append(event_id)
        row_count, row_count_basis = _row_count_detail(row)
        if row_count is None:
            summary["missing_row_count_entries"] += 1
        else:
            summary["total_rows"] += row_count
            summary["row_count_basis_counts"][row_count_basis] = (
                int(summary["row_count_basis_counts"].get(row_count_basis, 0)) + 1
            )
            if row_count_basis != "manifest_source_rows":
                summary["non_source_row_count_entries"] += 1
        summary["features"].extend(_row_features(row))
        summary["paths"].extend(_strings_from_fields(row, _PATH_FIELDS))
        summary["hashes"].extend(_strings_from_fields(row, _HASH_FIELDS))

    ordered: dict[str, dict[str, Any]] = {}
    for symbol in sorted(inventory):
        summary = inventory[symbol]
        if summary["manifest_row_count"] == summary["missing_row_count_entries"]:
            total_rows: int | None = None
        else:
            total_rows = int(summary["total_rows"])
        ordered[symbol] = {
            "manifest_row_count": int(summary["manifest_row_count"]),
            "event_ids": sorted(set(summary["event_ids"])),
            "total_rows": total_rows,
            "missing_row_count_entries": int(summary["missing_row_count_entries"]),
            "non_source_row_count_entries": int(summary["non_source_row_count_entries"]),
            "row_count_basis_counts": dict(sorted(summary["row_count_basis_counts"].items())),
            "features": sorted(set(summary["features"])),
            "paths": sorted(set(summary["paths"])),
            "hashes": sorted(set(summary["hashes"])),
        }
    return ordered


def _manifest_source_row_fingerprint(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    entries = []
    for row in rows:
        row_count, basis = _row_count_detail(row)
        entries.append(
            {
                "symbol": _first_non_empty(row, _SYMBOL_FIELDS) or "UNKNOWN",
                "event_id": _first_non_empty(row, _EVENT_FIELDS),
                "store_path": _first_non_empty(row, _PATH_FIELDS),
                "row_count": row_count,
                "row_count_basis": basis,
                "content_hash": _first_non_empty(row, _HASH_FIELDS),
                "feature_index_hash": _first_non_empty(row, _FEATURE_INDEX_HASH_FIELDS),
            }
        )
    entries.sort(
        key=lambda item: (
            str(item["symbol"]),
            str(item["event_id"]),
            str(item["store_path"]),
            str(item["content_hash"]),
            str(item["feature_index_hash"]),
        )
    )
    digest = hashlib.sha256(
        "\n".join(
            "|".join(
                [
                    str(item["symbol"]),
                    str(item["event_id"]),
                    str(item["store_path"]),
                    str(item["row_count"]),
                    str(item["row_count_basis"]),
                    str(item["content_hash"]),
                    str(item["feature_index_hash"]),
                ]
            )
            for item in entries
        ).encode("utf-8")
    ).hexdigest()
    return {
        "sha256": digest,
        "entry_count": len(entries),
        "source_row_count": sum(
            int(item["row_count"] or 0)
            for item in entries
            if item["row_count_basis"] == "manifest_source_rows"
        ),
    }


def _row_count(row: Mapping[str, Any]) -> int | None:
    row_count, _basis = _row_count_detail(row)
    return row_count


def _row_count_detail(row: Mapping[str, Any]) -> tuple[int | None, str]:
    for field in _SOURCE_ROW_COUNT_FIELDS:
        parsed = _optional_non_negative_int(row.get(field))
        if parsed is not None:
            return parsed, "manifest_source_rows"
    summary = row.get("row_summary")
    if isinstance(summary, Mapping):
        for field in _SOURCE_ROW_COUNT_FIELDS:
            parsed = _optional_non_negative_int(summary.get(field))
            if parsed is not None:
                return parsed, "manifest_source_rows"
    for field in _GENERIC_ROW_COUNT_FIELDS:
        parsed = _optional_non_negative_int(row.get(field))
        if parsed is not None:
            return parsed, "ambiguous_rows"
    if isinstance(summary, Mapping):
        for field in _GENERIC_ROW_COUNT_FIELDS:
            parsed = _optional_non_negative_int(summary.get(field))
            if parsed is not None:
                return parsed, "ambiguous_rows"
        for field in _BUILT_ROW_COUNT_FIELDS:
            parsed = _optional_non_negative_int(summary.get(field))
            if parsed is not None:
                return parsed, "built_rows"
    for field in _BUILT_ROW_COUNT_FIELDS:
        parsed = _optional_non_negative_int(row.get(field))
        if parsed is not None:
            return parsed, "built_rows"
    return None, "missing"


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or isinstance(value, Mapping):
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _row_features(row: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in _FEATURE_FIELDS:
        raw = row.get(field)
        if isinstance(raw, Mapping):
            values.extend(str(key).strip() for key in raw if str(key).strip())
        elif isinstance(raw, str):
            if raw.strip():
                values.append(raw.strip())
        elif isinstance(raw, Sequence):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return _dedupe(values)


def _strings_from_fields(row: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    values = []
    for field in fields:
        raw = row.get(field)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return values


def _first_non_empty(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    for field in fields:
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _clean_unique_strings(values: Sequence[str], label: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{label} must be a sequence of strings")
    return _dedupe(str(value).strip() for value in values if str(value).strip())


def _clean_string(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _non_negative_float(value: float, label: str) -> float:
    parsed = _finite_float(value, label)
    if parsed < 0.0:
        raise ValueError(f"{label} must be non-negative")
    return parsed


def _positive_float(value: float, label: str) -> float:
    parsed = _finite_float(value, label)
    if parsed <= 0.0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _optional_positive_float(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    return _positive_float(value, label)


def _positive_int(value: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _finite_float(value: float, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite numeric")
    return parsed


def _round_hours(value: float) -> float:
    return round(float(value), 6)


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


__all__ = [
    "RL_CAMPAIGN_BUDGET_SCHEMA_VERSION",
    "plan_rl_campaign_budget",
    "select_stratified_pilot_rows",
]
