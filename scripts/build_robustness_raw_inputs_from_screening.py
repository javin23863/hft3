#!/usr/bin/env python3
"""Build raw robustness inputs from a measured VectorBT screening artifact.

This is an assembler, not a robustness calculator. It only packages measured
parameter-surface rows that already exist in a screening artifact and fails
closed when the family surface is incomplete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.surface_stability import compute_surface_stability
from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    screening_status_text,
    validate_screening_artifact,
)

RAW_SCHEMA = "hft3_robustness_raw_inputs_v1"
SENSITIVITY_REPORT_SCHEMA = "hft3_robustness_bridge_sensitivity_report_v1"
FEATURE_RECIPE_HASH_POLICY = "event_specific_hash_bound_per_candidate"
EVENT_DATE_RE = re.compile(r"(20\d{2})_(\d{2})_(\d{2})")
ROBUSTNESS_SCOPE = "assembled_screening_surface_evidence"
SURFACE_POLICIES = (
    "current_first_event",
    "pooled_train_events",
    "median_event_surface",
    "fold_is_surface",
)
CORRECTED_SURFACE_POLICIES = tuple(
    policy for policy in SURFACE_POLICIES if policy != "current_first_event"
)
DEFAULT_SENSITIVITY_REPORT_OUT = (
    _REPO / "runtime" / "robustness" / "robustness_bridge_sensitivity_report.json"
)
SURFACE_NUMERIC_FIELDS = (
    "plateau_score",
    "plateau_width",
    "neighbor_stability",
    "cliff_distance_from_loss_regions",
    "parameter_perturbation_sensitivity",
    "peak_vs_plateau_comparison",
    "minimum_sample_size",
)


@dataclass(frozen=True)
class MeasuredRow:
    candidate_id: str
    screening_status: str
    family_key: tuple[str, str, str, str, str]
    family_key_map: dict[str, str]
    event_id: str
    event_date: date
    parameter_hash: str
    parameter_values: dict[str, Any]
    feature_recipe_hash: str
    expectancy: float
    net_return: float
    net_pnl: float | None
    sharpe: float
    profit_factor: float
    profit_factor_missing: bool
    max_drawdown: float
    trade_count: int


@dataclass
class ScreeningEvidence:
    artifact: dict[str, Any]
    source_path: str
    promoted_count: int
    promoted_by_id: dict[str, Mapping[str, Any]]
    promoted_measured: dict[str, MeasuredRow]
    family_rows: dict[tuple[str, str, str, str, str], list[MeasuredRow]]
    row_skip_reasons: Counter[str]
    diagnostic_only_source: bool
    artifacts_by_candidate: dict[str, Mapping[str, Any]]
    artifact_sources_by_candidate: dict[str, str]


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _error(reason: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"status": "error", "reason": reason}
    payload.update(extra)
    print(_compact_json(payload), file=sys.stderr)
    return 2


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _value_counts(values: Mapping[str, str]) -> dict[str, int]:
    return dict(Counter(str(value) for value in values.values()))


def _sample_mapping(values: Mapping[str, str], *, limit: int = 20) -> dict[str, str]:
    return {key: values[key] for key in sorted(values)[:limit]}


def _failure_diagnostics(
    *,
    packaged_count: int,
    min_packaged: int,
    row_skip_reasons: Counter[str],
    family_skips: Mapping[str, str],
    candidate_skips: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "raw_input_count_below_min",
        "packaged_count": packaged_count,
        "min_packaged": min_packaged,
        "row_skip_counts": dict(row_skip_reasons),
        "family_skip_counts": _value_counts(family_skips),
        "candidate_skip_counts": _value_counts(candidate_skips),
        "family_skip_sample": _sample_mapping(family_skips),
        "candidate_skip_sample": _sample_mapping(candidate_skips),
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label}_missing:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}_invalid_json:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}_must_be_object:{path}")
    return payload


def _meta(row: Mapping[str, Any], metrics: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("base_candidate_metadata")
    if isinstance(value, Mapping):
        return value
    value = metrics.get("base_candidate_metadata")
    return value if isinstance(value, Mapping) else {}


def _metric_source(row: Mapping[str, Any]) -> dict[str, Any]:
    metrics = {}
    raw_metrics = row.get("metric_values")
    if isinstance(raw_metrics, Mapping):
        metrics.update(dict(raw_metrics))
    metrics.update(dict(row))
    return metrics


def _not_run(value: Any) -> bool:
    return isinstance(value, Mapping) and screening_status_text(value) == "not_run"


def _number(value: Any) -> float | None:
    if value in (None, "") or _not_run(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _first_number(metrics: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        parsed = _number(metrics.get(name))
        if parsed is not None:
            return parsed
    vbt_stats = metrics.get("vbt_stats")
    if isinstance(vbt_stats, Mapping):
        for name in names:
            parsed = _number(vbt_stats.get(name))
            if parsed is not None:
                return parsed
    return None


def _net_return_fraction(metrics: Mapping[str, Any]) -> float | None:
    value = _first_number(metrics, "net_return")
    if value is not None:
        return value
    pct = _first_number(metrics, "net_return_pct", "Total Return [%]", "Total Return")
    if pct is not None:
        return pct / 100.0
    return None


def _event_id_from(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    value = metadata.get("event_id") or metadata.get("target_event_id")
    if value not in (None, ""):
        return str(value)
    base = str(row.get("base_candidate_id") or "")
    parts = base.split("|")
    if len(parts) >= 3:
        return parts[2]
    return ""


def _event_date(event_id: str) -> date | None:
    match = EVENT_DATE_RE.search(event_id)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _family_key(
    row: Mapping[str, Any],
    metrics: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str, str], dict[str, str]]:
    model_id = str(row.get("model_id") or row.get("hypothesis_id") or metrics.get("model_id") or "")
    symbol = str(metadata.get("symbol") or row.get("symbol") or "").strip()
    if symbol.lower() == "unknown":
        symbol = ""
    event_type = str(metadata.get("event_type") or row.get("target_event_type_or_null") or row.get("opportunity_type_or_event_type") or "")
    research_clock = str(metadata.get("research_clock") or row.get("research_clock") or "")
    context_set_id = str(metadata.get("context_set_id") or metadata.get("allowed_context_set_id") or row.get("allowed_context_set_id_or_null") or "")
    mapping = {
        "model_id": model_id,
        "symbol": symbol,
        "event_type": event_type,
        "research_clock": research_clock,
        "context_set_id": context_set_id,
    }
    return (model_id, symbol, event_type, research_clock, context_set_id), mapping


def _parameter_values(row: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any] | None:
    for value in (row.get("parameter_values"), row.get("param_values"), metrics.get("parameter_values"), metrics.get("param_values")):
        if isinstance(value, Mapping) and value:
            return dict(value)
    return None


def _extract_measured_row(row: Mapping[str, Any]) -> tuple[MeasuredRow | None, str | None]:
    metrics = _metric_source(row)
    metadata = _meta(row, metrics)
    event_id = _event_id_from(row, metadata)
    parsed_date = _event_date(event_id)
    if parsed_date is None:
        return None, "event_date_unparseable"
    parameter_hash = str(row.get("parameter_values_hash") or "")
    params = _parameter_values(row, metrics)
    if not parameter_hash or params is None:
        return None, "parameter_values_missing"
    net_return = _net_return_fraction(metrics)
    net_pnl = _first_number(metrics, "net_pnl")
    expectancy = _first_number(metrics, "expectancy_per_trade", "expectancy", "oos_expectancy")
    sharpe = _first_number(metrics, "sharpe", "Sharpe Ratio")
    max_drawdown = _first_number(metrics, "max_drawdown", "max_drawdown_pct", "Max Drawdown [%]")
    vbt_stats = metrics.get("vbt_stats")
    if isinstance(vbt_stats, Mapping):
        if net_return is None:
            total_return_pct = _number(vbt_stats.get("Total Return [%]"))
            if total_return_pct is not None:
                net_return = total_return_pct / 100.0
        if expectancy is None:
            expectancy = _number(vbt_stats.get("Expectancy"))
        if sharpe is None:
            sharpe = _number(vbt_stats.get("Sharpe Ratio"))
        if max_drawdown is None:
            official_drawdown = _number(vbt_stats.get("Max Drawdown [%]"))
            if official_drawdown is not None:
                max_drawdown = -official_drawdown if official_drawdown > 0 else official_drawdown
            elif "Max Drawdown [%]" in vbt_stats and vbt_stats.get("Max Drawdown [%]") is None:
                max_drawdown = 0.0
    trade_count = _integer(metrics.get("trade_count"))
    if trade_count is None:
        trade_count = _integer(metrics.get("num_trades"))
    if trade_count is None:
        if isinstance(vbt_stats, Mapping):
            trade_count = _integer(vbt_stats.get("Total Trades"))
    if trade_count == 0 and net_return == 0.0 and isinstance(vbt_stats, Mapping):
        if expectancy is None:
            expectancy = 0.0
        if sharpe is None:
            sharpe = 0.0
        if max_drawdown is None:
            max_drawdown = 0.0
    if None in (net_return, expectancy, sharpe, max_drawdown) or trade_count is None:
        return None, "measured_metrics_missing"
    profit_factor = _first_number(metrics, "profit_factor", "Profit Factor")
    profit_factor_missing = profit_factor is None
    if profit_factor is None:
        profit_factor = 0.0
    key, key_map = _family_key(row, metrics, metadata)
    if any(not part for part in key):
        return None, "family_key_missing"
    feature_recipe_hash = str(
        row.get("feature_recipe_hash")
        or metrics.get("feature_recipe_hash")
        or metadata.get("feature_recipe_hash")
        or ""
    )
    return (
        MeasuredRow(
            candidate_id=str(row.get("candidate_id") or ""),
            screening_status=str(row.get("screening_status") or ""),
            family_key=key,
            family_key_map=key_map,
            event_id=event_id,
            event_date=parsed_date,
            parameter_hash=parameter_hash,
            parameter_values=params,
            feature_recipe_hash=feature_recipe_hash,
            expectancy=float(expectancy),
            net_return=float(net_return),
            net_pnl=float(net_pnl) if net_pnl is not None else None,
            sharpe=float(sharpe),
            profit_factor=float(profit_factor),
            profit_factor_missing=profit_factor_missing,
            max_drawdown=float(max_drawdown),
            trade_count=int(trade_count),
        ),
        None,
    )


def _mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _lower_quartile(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[math.floor((len(ordered) - 1) * 0.25)])


def _aggregate(rows: list[MeasuredRow]) -> dict[str, float]:
    return {
        "sharpe": _mean([r.sharpe for r in rows]),
        "net_return": _mean([r.net_return for r in rows]),
        "net_return_adjusted": _mean([r.net_return for r in rows]),
        "profit_factor": _mean([r.profit_factor for r in rows]),
        "max_drawdown": min((r.max_drawdown for r in rows), default=0.0),
        "max_drawdown_adj_return": min((r.max_drawdown for r in rows), default=0.0),
        "trade_count": float(sum(r.trade_count for r in rows)),
    }


def _build_folds(events: list[date], n_folds: int) -> list[tuple[list[date], list[date]]]:
    if len(events) < n_folds + 1:
        return []
    first_test = len(events) - n_folds
    return [(events[:idx], [events[idx]]) for idx in range(first_test, len(events))]


def _surface_key_maps(rows: list[MeasuredRow]) -> dict[str, tuple[int, ...]]:
    param_names = sorted({name for row in rows for name in row.parameter_values})
    by_name: dict[str, dict[str, int]] = {}
    for name in param_names:
        encoded = sorted({
            json.dumps(row.parameter_values.get(name), sort_keys=True, default=str)
            for row in rows
        })
        by_name[name] = {value: idx for idx, value in enumerate(encoded)}
    out: dict[str, tuple[int, ...]] = {}
    for row in rows:
        out[row.parameter_hash] = tuple(
            by_name[name][json.dumps(row.parameter_values.get(name), sort_keys=True, default=str)]
            for name in param_names
        )
    return out


def _compute_surface(rows_by_pair: dict[tuple[date, str], MeasuredRow], events: list[date], params: list[str]) -> dict[str, Any]:
    train_event = events[0]
    first_rows = [rows_by_pair[(train_event, ph)] for ph in params]
    key_by_hash = _surface_key_maps(first_rows)
    grid = {
        key_by_hash[row.parameter_hash]: {
            "net_return": row.net_return,
            "trade_count": row.trade_count,
        }
        for row in first_rows
    }
    return compute_surface_stability(grid, performance_metric="net_return")


def _compute_pooled_surface(
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
) -> dict[str, Any]:
    first_rows = [rows_by_pair[(events[0], ph)] for ph in params]
    key_by_hash = _surface_key_maps(first_rows)
    grid = {}
    for row in first_rows:
        param_rows = [rows_by_pair[(event, row.parameter_hash)] for event in events]
        grid[key_by_hash[row.parameter_hash]] = {
            "net_return": _median([r.net_return for r in param_rows]),
            "trade_count": int(_median([float(r.trade_count) for r in param_rows])),
        }
    surface = compute_surface_stability(grid, performance_metric="net_return")
    surface["surface_policy"] = "pooled_train_events"
    surface["aggregation_method"] = "median_by_parameter_cell"
    surface["aggregation_event_count"] = len(events)
    return surface


def _summarise_surfaces(
    surfaces: list[Mapping[str, Any]],
    *,
    policy: str,
) -> dict[str, Any]:
    if not surfaces:
        return {
            "status": "fail",
            "formula_authority_status": "defined",
            "literature_or_ontology_citation": "",
            "required_checks": [],
            "surface_policy": policy,
            "reason": "no_usable_surfaces",
            **{field: 0 for field in SURFACE_NUMERIC_FIELDS},
        }
    first = surfaces[0]
    numeric: dict[str, float | int] = {}
    for field in SURFACE_NUMERIC_FIELDS:
        values = [_number(surface.get(field)) for surface in surfaces]
        finite = [float(value) for value in values if value is not None]
        value = _median(finite)
        if field in {"plateau_width", "cliff_distance_from_loss_regions", "minimum_sample_size"}:
            numeric[field] = int(round(value))
        else:
            numeric[field] = round(float(value), 6)
    status = "fail" if any(
        (
            float(numeric["neighbor_stability"]) < 0.5,
            float(numeric["parameter_perturbation_sensitivity"]) > 0.3,
            int(numeric["cliff_distance_from_loss_regions"]) < 2,
            float(numeric["peak_vs_plateau_comparison"]) > 1.3,
            int(numeric["minimum_sample_size"]) < 30,
        )
    ) else "pass"
    plateau_scores = [
        float(value)
        for surface in surfaces
        for value in [_number(surface.get("plateau_score"))]
        if value is not None
    ]
    surface_pass_count = sum(
        1 for surface in surfaces if screening_status_text(surface) == "pass"
    )
    return {
        "status": status,
        "formula_authority_status": str(
            first.get("formula_authority_status") or "defined"
        ),
        "literature_or_ontology_citation": str(
            first.get("literature_or_ontology_citation") or ""
        ),
        "required_checks": list(first.get("required_checks") or []),
        **numeric,
        "surface_policy": policy,
        "surface_count": len(surfaces),
        "surface_pass_count": surface_pass_count,
        "median_plateau_score": round(_median(plateau_scores), 6),
        "downside_plateau_score": round(_lower_quartile(plateau_scores), 6),
        "event_surface_dispersion": round(
            float(statistics.pstdev(plateau_scores)) if len(plateau_scores) > 1 else 0.0,
            6,
        ),
    }


def _compute_median_event_surface(
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
) -> dict[str, Any]:
    surfaces = [_compute_surface(rows_by_pair, [event], params) for event in events]
    return _summarise_surfaces(surfaces, policy="median_event_surface")


def _best_param_by_net_return(
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
) -> str:
    return max(
        params,
        key=lambda parameter_hash: _median([
            rows_by_pair[(event, parameter_hash)].net_return for event in events
        ]),
    )


def _compute_fold_surface(
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
    folds: list[tuple[list[date], list[date]]],
) -> dict[str, Any]:
    surfaces: list[Mapping[str, Any]] = []
    persistence_rows: list[dict[str, Any]] = []
    for fold_index, (train_events, test_events) in enumerate(folds):
        surface = _compute_pooled_surface(rows_by_pair, train_events, params)
        surface["surface_policy"] = "fold_is_surface"
        surfaces.append(surface)
        selected_param = _best_param_by_net_return(rows_by_pair, train_events, params)
        oos_by_param = {
            parameter_hash: _mean([
                rows_by_pair[(event, parameter_hash)].net_return for event in test_events
            ])
            for parameter_hash in params
        }
        selected_oos = oos_by_param[selected_param]
        persisted = selected_oos > 0.0 and selected_oos >= _median(list(oos_by_param.values()))
        persistence_rows.append({
            "fold_id": f"fold_{fold_index}",
            "selected_parameter_hash": selected_param,
            "selected_oos_net_return": round(selected_oos, 8),
            "median_oos_net_return": round(_median(list(oos_by_param.values())), 8),
            "selected_region_persisted_oos": persisted,
        })
    summary = _summarise_surfaces(surfaces, policy="fold_is_surface")
    persisted_count = sum(1 for row in persistence_rows if row["selected_region_persisted_oos"])
    summary["fold_count"] = len(folds)
    summary["fold_oos_persistence"] = persistence_rows
    summary["selected_region_oos_persistence_ratio"] = (
        round(persisted_count / len(persistence_rows), 6) if persistence_rows else 0.0
    )
    if persistence_rows and persisted_count < math.ceil(len(persistence_rows) / 2):
        summary["status"] = "fail"
        summary["reason"] = "selected_region_oos_persistence_below_majority"
    return summary


def _compute_surface_for_policy(
    *,
    policy: str,
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
    folds: list[tuple[list[date], list[date]]],
) -> dict[str, Any]:
    if policy == "current_first_event":
        return _compute_surface(rows_by_pair, events, params)
    if policy == "pooled_train_events":
        return _compute_pooled_surface(rows_by_pair, events, params)
    if policy == "median_event_surface":
        return _compute_median_event_surface(rows_by_pair, events, params)
    if policy == "fold_is_surface":
        return _compute_fold_surface(rows_by_pair, events, params, folds)
    raise ValueError(f"unsupported_surface_policy:{policy}")


def _training_events_from_folds(
    events: list[date],
    folds: list[tuple[list[date], list[date]]],
) -> list[date]:
    train_events = sorted({event for fold_train, _fold_test in folds for event in fold_train})
    return train_events or events


def _p_value(expectancies: list[float]) -> float:
    n = len(expectancies)
    if n < 2:
        return 1.0
    mean = statistics.fmean(expectancies)
    stdev = statistics.stdev(expectancies)
    if stdev <= 1e-15:
        return 0.0 if mean > 0 else 1.0
    z = mean / (stdev / math.sqrt(n))
    return max(0.0, min(1.0, 0.5 * math.erfc(z / math.sqrt(2.0))))


def _source_path(path: Path, source_root: Path | None) -> str:
    resolved = path.resolve()
    if source_root is None:
        return str(path)
    try:
        return str(resolved.relative_to(source_root.resolve()))
    except ValueError:
        return str(resolved)


def _unit_artifact_record_path(artifact_path: Path, artifact_dir: Path) -> str:
    try:
        return artifact_path.resolve().relative_to(artifact_dir.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"unit_artifact_outside_dir:{artifact_path}") from exc


def _collect_artifact_rows(
    artifact: Mapping[str, Any],
    *,
    artifact_source: str,
    promoted_by_id: dict[str, Mapping[str, Any]],
    promoted_measured: dict[str, MeasuredRow],
    family_rows: dict[tuple[str, str, str, str, str], list[MeasuredRow]],
    row_skip_reasons: Counter[str],
    artifacts_by_candidate: dict[str, Mapping[str, Any]],
    artifact_sources_by_candidate: dict[str, str],
) -> int:
    promoted_rows = [row for row in artifact.get("promoted", []) if isinstance(row, Mapping)]
    rejected_rows = [row for row in artifact.get("rejected", []) if isinstance(row, Mapping)]
    measured_by_candidate: dict[str, MeasuredRow] = {}
    for row in promoted_rows:
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        if candidate_id in promoted_by_id:
            raise ValueError(f"duplicate_promoted_candidate_id_across_artifacts:{candidate_id}")
        promoted_by_id[candidate_id] = row
        artifacts_by_candidate[candidate_id] = artifact
        artifact_sources_by_candidate[candidate_id] = artifact_source

    for row in [*promoted_rows, *rejected_rows]:
        measured, reason = _extract_measured_row(row)
        if measured is None:
            if reason == "family_key_missing":
                candidate_id = str(row.get("candidate_id") or "unknown_candidate")
                raise ValueError(f"family_key_missing:{candidate_id}")
            row_skip_reasons[str(reason or "row_unusable")] += 1
            continue
        family_rows[measured.family_key].append(measured)
        measured_by_candidate[measured.candidate_id] = measured

    for row in promoted_rows:
        measured = measured_by_candidate.get(str(row.get("candidate_id") or ""))
        if measured is not None:
            promoted_measured[measured.candidate_id] = measured
    return len(promoted_rows)


def _artifact_identity_hash(artifact: Mapping[str, Any]) -> str:
    value = artifact.get("screening_artifact_hash")
    if value:
        return str(value)
    return hashlib.sha256(_compact_json(artifact).encode("utf-8")).hexdigest()


def _unit_artifact_set_hash(records: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        _compact_json(
            {
                "schema": "hft3_unit_screening_artifact_set_v1",
                "artifacts": records,
            }
        ).encode("utf-8")
    ).hexdigest()


def _load_screening_evidence(
    *,
    screening_artifact_path: Path | None,
    screening_artifact_dir: Path | None,
    source_root: Path | None,
) -> ScreeningEvidence:
    if (screening_artifact_path is None) == (screening_artifact_dir is None):
        raise ValueError("provide_exactly_one_screening_artifact_source")

    promoted_by_id: dict[str, Mapping[str, Any]] = {}
    promoted_measured: dict[str, MeasuredRow] = {}
    family_rows: dict[tuple[str, str, str, str, str], list[MeasuredRow]] = defaultdict(list)
    row_skip_reasons: Counter[str] = Counter()
    artifacts_by_candidate: dict[str, Mapping[str, Any]] = {}
    artifact_sources_by_candidate: dict[str, str] = {}

    if screening_artifact_path is not None:
        artifact = _load_json_object(screening_artifact_path, "screening_artifact")
        validate_screening_artifact(artifact)
        source_path = _source_path(screening_artifact_path, source_root)
        promoted_count = _collect_artifact_rows(
            artifact,
            artifact_source=source_path,
            promoted_by_id=promoted_by_id,
            promoted_measured=promoted_measured,
            family_rows=family_rows,
            row_skip_reasons=row_skip_reasons,
            artifacts_by_candidate=artifacts_by_candidate,
            artifact_sources_by_candidate=artifact_sources_by_candidate,
        )
        return ScreeningEvidence(
            artifact=artifact,
            source_path=source_path,
            promoted_count=promoted_count,
            promoted_by_id=promoted_by_id,
            promoted_measured=promoted_measured,
            family_rows=family_rows,
            row_skip_reasons=row_skip_reasons,
            diagnostic_only_source=False,
            artifacts_by_candidate=artifacts_by_candidate,
            artifact_sources_by_candidate=artifact_sources_by_candidate,
        )

    assert screening_artifact_dir is not None
    artifact_paths = sorted(screening_artifact_dir.glob("**/screening_artifact.json"))
    if not artifact_paths:
        raise ValueError(f"screening_artifact_dir_empty:{screening_artifact_dir}")

    artifact_records: list[dict[str, str]] = []
    promoted_count = 0
    for artifact_path in artifact_paths:
        artifact = _load_json_object(artifact_path, "screening_artifact")
        validate_screening_artifact(artifact)
        source_path = _source_path(artifact_path, source_root)
        artifact_records.append(
            {
                "path": _unit_artifact_record_path(artifact_path, screening_artifact_dir),
                "screening_artifact_hash": _artifact_identity_hash(artifact),
            }
        )
        promoted_count += _collect_artifact_rows(
            artifact,
            artifact_source=source_path,
            promoted_by_id=promoted_by_id,
            promoted_measured=promoted_measured,
            family_rows=family_rows,
            row_skip_reasons=row_skip_reasons,
            artifacts_by_candidate=artifacts_by_candidate,
            artifact_sources_by_candidate=artifact_sources_by_candidate,
        )

    artifact_set_hash = _unit_artifact_set_hash(artifact_records)
    return ScreeningEvidence(
        artifact={
            "screening_artifact_hash": artifact_set_hash,
            "screening_artifact_source": "unit_artifact_directory",
            "unit_artifact_count": len(artifact_paths),
            "unit_artifact_set_hash": artifact_set_hash,
        },
        source_path=_source_path(screening_artifact_dir, source_root),
        promoted_count=promoted_count,
        promoted_by_id=promoted_by_id,
        promoted_measured=promoted_measured,
        family_rows=family_rows,
        row_skip_reasons=row_skip_reasons,
        diagnostic_only_source=True,
        artifacts_by_candidate=artifacts_by_candidate,
        artifact_sources_by_candidate=artifact_sources_by_candidate,
    )


def _build_wfc_rows(
    *,
    rows_by_pair: dict[tuple[date, str], MeasuredRow],
    events: list[date],
    params: list[str],
    folds: list[tuple[list[date], list[date]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fold_index, (train_events, test_events) in enumerate(folds):
        for parameter_hash in params:
            is_rows = [rows_by_pair[(event, parameter_hash)] for event in train_events]
            oos_rows = [rows_by_pair[(event, parameter_hash)] for event in test_events]
            rows.append({
                "parameter_hash": parameter_hash,
                "fold_id": f"fold_{fold_index}",
                "params": rows_by_pair[(events[0], parameter_hash)].parameter_values,
                "train_dates": [event.isoformat() for event in train_events],
                "test_dates": [event.isoformat() for event in test_events],
                "is_metrics": _aggregate(is_rows),
                "oos_metrics": _aggregate(oos_rows),
            })
    return rows


def _prepare_family_surface_inputs(
    *,
    family_rows: list[MeasuredRow],
    folds_requested: int,
    min_events: int,
    min_parameter_combinations: int,
    min_completeness: float,
) -> tuple[dict[str, Any] | None, str]:
    events = sorted({row.event_date for row in family_rows})
    params = sorted({row.parameter_hash for row in family_rows})
    if len(events) < min_events:
        return None, f"insufficient_events:{len(events)}<{min_events}"
    rows_by_pair: dict[tuple[date, str], MeasuredRow] = {}
    for row in family_rows:
        pair = (row.event_date, row.parameter_hash)
        if pair in rows_by_pair:
            return None, f"duplicate_event_parameter_cell:{row.event_id}:{row.parameter_hash}"
        rows_by_pair[pair] = row
    expected = len(events) * len(params)
    completeness = len(rows_by_pair) / expected if expected else 0.0
    if completeness + 1e-12 < min_completeness:
        return None, f"incomplete_event_parameter_surface:{completeness:.6f}<{min_completeness:.6f}"
    complete_params = [
        parameter_hash
        for parameter_hash in params
        if all((event, parameter_hash) in rows_by_pair for event in events)
    ]
    if len(complete_params) < min_parameter_combinations:
        return (
            None,
            "insufficient_complete_parameter_combinations:"
            f"{len(complete_params)}<{min_parameter_combinations}",
        )
    missing_count = expected - len(rows_by_pair)
    if missing_count and min_completeness >= 1.0:
        return None, f"missing_event_parameter_cells:{missing_count}"
    params = complete_params
    folds = _build_folds(events, folds_requested)
    if len(folds) < folds_requested:
        return None, f"insufficient_walk_forward_folds:{len(folds)}<{folds_requested}"
    return (
        {
            "events": events,
            "params": params,
            "rows_by_pair": rows_by_pair,
            "folds": folds,
            "n_trials": len(params),
            "missing_profit_factor_count": sum(1 for row in family_rows if row.profit_factor_missing),
        },
        "ok",
    )


def _build_family_payload(
    *,
    family_rows: list[MeasuredRow],
    folds_requested: int,
    min_events: int,
    min_parameter_combinations: int,
    min_completeness: float,
    surface_policy: str,
) -> tuple[dict[str, Any] | None, str]:
    prepared, reason = _prepare_family_surface_inputs(
        family_rows=family_rows,
        folds_requested=folds_requested,
        min_events=min_events,
        min_parameter_combinations=min_parameter_combinations,
        min_completeness=min_completeness,
    )
    if prepared is None:
        return None, reason
    events = prepared["events"]
    params = prepared["params"]
    rows_by_pair = prepared["rows_by_pair"]
    folds = prepared["folds"]
    surface = _compute_surface_for_policy(
        policy=surface_policy,
        rows_by_pair=rows_by_pair,
        events=events,
        params=params,
        folds=folds,
    )
    if screening_status_text(surface) != "pass":
        return None, "surface_stability_metrics_not_replay_ready"
    wfc_rows = _build_wfc_rows(
        rows_by_pair=rows_by_pair,
        events=events,
        params=params,
        folds=folds,
    )
    cscv_matrix = [
        [rows_by_pair[(event, parameter_hash)].net_return for parameter_hash in params]
        for event in events
    ]
    p_values = [
        _p_value([rows_by_pair[(event, parameter_hash)].expectancy for event in events])
        for parameter_hash in params
    ]
    by_param = {
        parameter_hash: [rows_by_pair[(event, parameter_hash)] for event in events]
        for parameter_hash in params
    }
    return (
        {
            "events": events,
            "params": params,
            "rows_by_pair": rows_by_pair,
            "wfc_rows": wfc_rows,
            "cscv_matrix": cscv_matrix,
            "surface_stability_metrics": surface,
            "p_values": p_values,
            "by_param": by_param,
            "n_trials": len(params),
            "missing_profit_factor_count": prepared["missing_profit_factor_count"],
        },
        "ok",
    )


def _family_key_map(key: tuple[str, str, str, str, str]) -> dict[str, str]:
    return dict(zip(("model_id", "symbol", "event_type", "research_clock", "context_set_id"), key))


def _promoted_family_candidate_ids(family_rows: list[MeasuredRow], params: list[str]) -> list[str]:
    param_set = set(params)
    return sorted(
        row.candidate_id
        for row in family_rows
        if screening_status_text(row.screening_status) == "pass"
        and row.parameter_hash in param_set
    )


def _event_quality_rejections(
    *,
    events: list[date],
    params: list[str],
    rows_by_pair: Mapping[tuple[date, str], MeasuredRow],
    event_id_by_date: Mapping[date, str],
) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for event in events:
        reasons: list[str] = []
        missing = [
            parameter_hash
            for parameter_hash in params
            if (event, parameter_hash) not in rows_by_pair
        ]
        insufficient_trade_cells = [
            parameter_hash
            for parameter_hash in params
            if (event, parameter_hash) in rows_by_pair
            and rows_by_pair[(event, parameter_hash)].trade_count <= 0
        ]
        if missing:
            reasons.append("missing_surface")
        if insufficient_trade_cells:
            reasons.append("insufficient_trades")
        if reasons:
            rejected.append({
                "event_id": event_id_by_date.get(event, ""),
                "event_date": event.isoformat(),
                "reasons": reasons,
                "missing_parameter_cell_count": len(missing),
                "insufficient_trade_cell_count": len(insufficient_trade_cells),
            })
    return rejected


def _family_sensitivity_report(
    *,
    family_rows: list[MeasuredRow],
    folds_requested: int,
    min_events: int,
    min_parameter_combinations: int,
    min_completeness: float,
) -> dict[str, Any]:
    events = sorted({row.event_date for row in family_rows})
    params = sorted({row.parameter_hash for row in family_rows})
    event_id_by_date: dict[date, str] = {}
    rows_by_pair_for_report: dict[tuple[date, str], MeasuredRow] = {}
    for row in family_rows:
        event_id_by_date.setdefault(row.event_date, row.event_id)
        rows_by_pair_for_report.setdefault((row.event_date, row.parameter_hash), row)
    event_rejections = _event_quality_rejections(
        events=events,
        params=params,
        rows_by_pair=rows_by_pair_for_report,
        event_id_by_date=event_id_by_date,
    )
    prepared, prepare_reason = _prepare_family_surface_inputs(
        family_rows=family_rows,
        folds_requested=folds_requested,
        min_events=min_events,
        min_parameter_combinations=min_parameter_combinations,
        min_completeness=min_completeness,
    )
    policy_passes: dict[str, bool] = {}
    policy_failure_reasons: dict[str, str] = {}
    policy_candidate_ids: dict[str, list[str]] = {}
    policy_metrics: dict[str, Any] = {}
    usable_event_count = max(0, len(events) - len(event_rejections))
    rejected_event_count = len(event_rejections)
    complete_params: list[str] = []
    training_events: list[date] = []
    if prepared is None:
        for policy in SURFACE_POLICIES:
            policy_passes[policy] = False
            policy_failure_reasons[policy] = prepare_reason
            policy_candidate_ids[policy] = []
            policy_metrics[policy] = {"status": "fail", "reason": prepare_reason}
    else:
        training_events = _training_events_from_folds(prepared["events"], prepared["folds"])
        complete_params = list(prepared["params"])
        for policy in SURFACE_POLICIES:
            policy_events = (
                training_events
                if policy in {"pooled_train_events", "median_event_surface"}
                else prepared["events"]
            )
            try:
                surface = _compute_surface_for_policy(
                    policy=policy,
                    rows_by_pair=prepared["rows_by_pair"],
                    events=policy_events,
                    params=prepared["params"],
                    folds=prepared["folds"],
                )
            except ValueError as exc:
                surface = {"status": "fail", "reason": str(exc)}
            passed = screening_status_text(surface) == "pass"
            policy_passes[policy] = passed
            policy_failure_reasons[policy] = (
                ""
                if passed
                else str(surface.get("reason") or "surface_stability_metrics_not_replay_ready")
            )
            policy_candidate_ids[policy] = _promoted_family_candidate_ids(
                family_rows,
                prepared["params"],
            ) if passed else []
            policy_metrics[policy] = surface

    current_ids = set(policy_candidate_ids["current_first_event"])
    corrected_ids = set().union(
        *(set(policy_candidate_ids[policy]) for policy in CORRECTED_SURFACE_POLICIES)
    )
    family_key = family_rows[0].family_key if family_rows else ("", "", "", "", "")
    report = {
        "model_family": _family_key_map(family_key),
        "vectorbt_promoted_count": sum(
            1 for row in family_rows if screening_status_text(row.screening_status) == "pass"
        ),
        "packaging_eligible": prepared is not None,
        "packaging_failure_reason": "" if prepared is not None else prepare_reason,
        "event_count": len(events),
        "usable_event_count": usable_event_count,
        "rejected_event_count": rejected_event_count,
        "rejected_events": event_rejections,
        "surface_training_event_count": len(training_events),
        "surface_training_event_ids": [
            event_id_by_date.get(event, "") for event in training_events
        ],
        "parameter_cell_count": len(family_rows),
        "complete_parameter_combination_count": len(complete_params),
        "event_0_id": event_id_by_date.get(events[0], "") if events else "",
        "event_0_surface_metrics": policy_metrics["current_first_event"],
        "pooled_surface_metrics": policy_metrics["pooled_train_events"],
        "median_event_surface_metrics": policy_metrics["median_event_surface"],
        "fold_is_surface_metrics": policy_metrics["fold_is_surface"],
        "surface_policy_passes": policy_passes,
        "policy_failure_reasons": policy_failure_reasons,
        "candidates_rejected_by_current_but_passed_by_corrected_policy": sorted(
            corrected_ids - current_ids
        ),
    }
    for policy in SURFACE_POLICIES:
        report[f"{policy}_pass"] = policy_passes[policy]
        report[f"candidates_passing_{policy}"] = len(policy_candidate_ids[policy])
        report[f"candidate_ids_passing_{policy}"] = policy_candidate_ids[policy]
    return report


def _build_sensitivity_report(
    *,
    source_path: str,
    artifact: Mapping[str, Any],
    selected_surface_policy: str,
    promoted_count: int,
    family_reports: list[dict[str, Any]],
    row_skip_reasons: Mapping[str, int],
    family_skips: Mapping[str, str],
    candidate_skips: Mapping[str, str],
    packaged_count: int,
    min_packaged: int,
) -> dict[str, Any]:
    corrected_ids = set().union(
        *(
            set(report.get(f"candidate_ids_passing_{policy}", []))
            for report in family_reports
            for policy in CORRECTED_SURFACE_POLICIES
        )
    ) if family_reports else set()
    current_ids = (
        set().union(
            *(
                set(report.get("candidate_ids_passing_current_first_event", []))
                for report in family_reports
            )
        )
        if family_reports
        else set()
    )
    summary = {
        "vectorbt_promoted_count": promoted_count,
        "model_family_count": len(family_reports),
        "packaging_eligible_family_count": sum(
            1 for report in family_reports if report.get("packaging_eligible") is True
        ),
        "packaged_count": packaged_count,
        "min_packaged": min_packaged,
        "current_first_event_family_pass_count": sum(
            1 for report in family_reports if report["current_first_event_pass"]
        ),
        "pooled_train_events_family_pass_count": sum(
            1 for report in family_reports if report["pooled_train_events_pass"]
        ),
        "median_event_surface_family_pass_count": sum(
            1 for report in family_reports if report["median_event_surface_pass"]
        ),
        "fold_is_surface_family_pass_count": sum(
            1 for report in family_reports if report["fold_is_surface_pass"]
        ),
        "candidates_passing_current_first_event": sum(
            report["candidates_passing_current_first_event"] for report in family_reports
        ),
        "candidates_passing_pooled_train_events": sum(
            report["candidates_passing_pooled_train_events"] for report in family_reports
        ),
        "candidates_passing_median_event_surface": sum(
            report["candidates_passing_median_event_surface"] for report in family_reports
        ),
        "candidates_passing_fold_is_surface": sum(
            report["candidates_passing_fold_is_surface"] for report in family_reports
        ),
        "candidates_rejected_by_current_but_passed_by_corrected_policy": sorted(
            corrected_ids - current_ids
        ),
        "hftbacktest_eligible_candidates": 0,
    }
    return {
        "schema": SENSITIVITY_REPORT_SCHEMA,
        "screening_artifact": source_path,
        "screening_artifact_hash": artifact.get("screening_artifact_hash"),
        "screening_artifact_source": artifact.get("screening_artifact_source", "single_artifact"),
        "unit_artifact_count": artifact.get("unit_artifact_count"),
        "unit_artifact_set_hash": artifact.get("unit_artifact_set_hash"),
        "selected_surface_policy": selected_surface_policy,
        "baseline_surface_policy": "current_first_event",
        "summary": summary,
        "families": family_reports,
        "assembler_diagnostics": {
            "row_skip_counts": dict(row_skip_reasons),
            "family_skip_counts": _value_counts(family_skips),
            "candidate_skip_counts": _value_counts(candidate_skips),
            "family_skip_sample": _sample_mapping(family_skips),
            "candidate_skip_sample": _sample_mapping(candidate_skips),
        },
        "attrition": {
            "vectorbt_promoted_candidates": promoted_count,
            "model_families": len(family_reports),
            "families_with_enough_events_cells_trades_data": summary[
                "packaging_eligible_family_count"
            ],
            "selected_policy_packaged_candidates": packaged_count,
            "current_first_event_surface_survivors": summary["candidates_passing_current_first_event"],
            "corrected_policy_surface_survivors": len(corrected_ids),
            "wfc_walk_forward_survivors": 0,
            "cscv_pbo_survivors": 0,
            "dsr_survivors": 0,
            "hftbacktest_eligible_candidates": 0,
        },
    }


def build_robustness_raw_inputs_from_screening(
    *,
    screening_artifact_path: Path | None,
    screening_artifact_dir: Path | None = None,
    out_path: Path,
    source_root: Path | None,
    folds: int,
    min_events: int,
    min_parameter_combinations: int,
    min_completeness: float,
    min_packaged: int,
    fee_per_rt: float | None,
    tick_value: float | None,
    fees_from_model: bool = False,
    diagnostics_out: Path | None = None,
    surface_policy: str = "current_first_event",
    sensitivity_report_out: Path | None = None,
) -> dict[str, Any]:
    if surface_policy not in SURFACE_POLICIES:
        raise ValueError(f"unsupported_surface_policy:{surface_policy}")
    if fees_from_model and (fee_per_rt is not None or tick_value is not None):
        raise ValueError("fees_from_model_conflicts_with_explicit_fee_args")
    diagnostic_only_policy = surface_policy != "current_first_event"
    diagnostic_only_source = screening_artifact_dir is not None
    if (diagnostic_only_policy or diagnostic_only_source) and sensitivity_report_out is None:
        sensitivity_report_out = DEFAULT_SENSITIVITY_REPORT_OUT
    if min_packaged < 0:
        raise ValueError("min_packaged_must_be_non_negative")
    if folds <= 0:
        raise ValueError("folds_must_be_positive")
    if min_events <= 1:
        raise ValueError("min_events_must_exceed_one")
    if min_parameter_combinations <= 0:
        raise ValueError("min_parameter_combinations_must_be_positive")
    if not (0.0 < min_completeness <= 1.0):
        raise ValueError("min_completeness_must_be_in_(0,1]")
    if screening_artifact_path is not None and screening_artifact_path.resolve() == out_path.resolve():
        raise ValueError("out_must_not_overwrite_screening_artifact")
    if screening_artifact_dir is not None and out_path.exists():
        raise ValueError(f"diagnostic_out_must_not_already_exist:{out_path}")
    evidence = _load_screening_evidence(
        screening_artifact_path=screening_artifact_path,
        screening_artifact_dir=screening_artifact_dir,
        source_root=source_root,
    )
    artifact = evidence.artifact
    source_path = evidence.source_path
    diagnostic_only_source = evidence.diagnostic_only_source
    diagnostic_only = diagnostic_only_policy or diagnostic_only_source
    family_rows = evidence.family_rows
    row_skip_reasons = evidence.row_skip_reasons
    promoted_by_id = evidence.promoted_by_id
    promoted_measured = evidence.promoted_measured

    family_payloads: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    family_skips: dict[str, str] = {}
    family_reports: list[dict[str, Any]] = []
    for key, rows in sorted(family_rows.items(), key=lambda item: item[0]):
        if sensitivity_report_out is not None:
            family_reports.append(
                _family_sensitivity_report(
                    family_rows=rows,
                    folds_requested=folds,
                    min_events=min_events,
                    min_parameter_combinations=min_parameter_combinations,
                    min_completeness=min_completeness,
                )
            )
        payload, reason = _build_family_payload(
            family_rows=rows,
            folds_requested=folds,
            min_events=min_events,
            min_parameter_combinations=min_parameter_combinations,
            min_completeness=min_completeness,
            surface_policy="current_first_event",
        )
        if payload is None:
            family_skips[_compact_json(_family_key_map(key))] = reason
            continue
        family_payloads[key] = payload

    candidates: dict[str, Any] = {}
    candidate_skips: dict[str, str] = {}
    explicit_stress = fee_per_rt is not None and tick_value is not None
    for candidate_id, measured in sorted(promoted_measured.items()):
        if candidate_id not in promoted_by_id:
            continue
        if diagnostic_only:
            if diagnostic_only_source:
                candidate_skips[candidate_id] = (
                    f"diagnostic_only_screening_artifact_dir:{surface_policy}"
                )
            else:
                candidate_skips[candidate_id] = (
                    f"diagnostic_only_surface_policy:{surface_policy}"
                )
            continue
        if explicit_stress:
            row_fee = float(fee_per_rt)
            row_tick = float(tick_value)
            stress_input_source = "cli_explicit"
        elif fees_from_model:
            derived = _fee_model_stress_inputs(measured.family_key_map.get("symbol", ""))
            if derived is None:
                candidate_skips[candidate_id] = (
                    "stress_decomposition_missing:fee_model_has_no_product:"
                    f"{measured.family_key_map.get('symbol', '')}"
                )
                continue
            row_fee, row_tick, product = derived
            stress_input_source = f"fee_model_derived:{product}"
        else:
            candidate_skips[candidate_id] = "stress_decomposition_missing"
            continue
        family = family_payloads.get(measured.family_key)
        if family is None:
            candidate_skips[candidate_id] = "family_surface_not_accepted"
            continue
        param_rows = family["by_param"].get(measured.parameter_hash)
        if not param_rows:
            candidate_skips[candidate_id] = "candidate_parameter_not_in_family_surface"
            continue
        n_events = len(param_rows)
        candidates[candidate_id] = {
            "family_key": measured.family_key_map,
            "feature_recipe_hash_policy": FEATURE_RECIPE_HASH_POLICY,
            "robustness_gate_scope": ROBUSTNESS_SCOPE,
            "source_evidence": {
                "screening_artifact": {
                    "path": source_path,
                },
            },
            "surface_stability_metrics": family["surface_stability_metrics"],
            "robustness_input": {
                "per_event_expectancies": [row.expectancy for row in param_rows],
                "n_trials": family["n_trials"],
                "cscv_matrix": family["cscv_matrix"],
                "wfc_rows": family["wfc_rows"],
                "wfc_cfg": {
                    "enabled": True,
                    "min_parameter_combinations": min_parameter_combinations,
                    "min_walk_forward_folds": folds,
                    "primary_metric": "sharpe",
                    "pearson_min": 0.20,
                    "spearman_min": 0.20,
                    "correlation_p_value_max": 0.10,
                    "min_positive_fold_ratio": 0.60,
                    "require_oos_net_profit_positive": True,
                    "require_oos_risk_adjusted_positive": True,
                    "max_oos_drawdown_limit": -500.0,
                    "permutation_samples": 100,
                    "bootstrap_samples": 100,
                    "outlier_winsor_pct": 0.01,
                },
                "per_event_n_trades": [row.trade_count for row in param_rows],
                "per_event_fee_per_rt": [row_fee] * n_events,
                "per_event_tick_value": [row_tick] * n_events,
                "p_values": family["p_values"],
            },
            "diagnostics": {
                "event_count": n_events,
                "parameter_combination_count": family["n_trials"],
                "missing_profit_factor_count": family["missing_profit_factor_count"],
                "stress_input_source": stress_input_source,
            },
        }

    if sensitivity_report_out is not None:
        _write_json(
            sensitivity_report_out,
            _build_sensitivity_report(
                source_path=source_path,
                artifact=artifact,
                selected_surface_policy=surface_policy,
                promoted_count=evidence.promoted_count,
                family_reports=family_reports,
                row_skip_reasons=row_skip_reasons,
                family_skips=family_skips,
                candidate_skips=candidate_skips,
                packaged_count=len(candidates),
                min_packaged=min_packaged,
            ),
        )

    if diagnostic_only:
        candidate_skip_payload = (
            _sample_mapping(candidate_skips)
            if diagnostic_only_source
            else candidate_skips
        )
        skipped: dict[str, Any] = {
            "rows": dict(row_skip_reasons),
            "families": family_skips,
            "candidates": candidate_skip_payload,
        }
        if diagnostic_only_source:
            skipped["candidate_skip_counts"] = _value_counts(candidate_skips)
            skipped["candidate_skip_sample"] = _sample_mapping(candidate_skips)
        return {
            "status": "diagnostic_only",
            "surface_policy": surface_policy,
            "source_mode": "screening_artifact_dir" if diagnostic_only_source else "screening_artifact",
            "sensitivity_report_out": str(sensitivity_report_out),
            "packaged_count": 0,
            "packaged_candidate_ids": [],
            "skipped": skipped,
        }

    if len(candidates) < min_packaged:
        diagnostics = _failure_diagnostics(
            packaged_count=len(candidates),
            min_packaged=min_packaged,
            row_skip_reasons=row_skip_reasons,
            family_skips=family_skips,
            candidate_skips=candidate_skips,
        )
        if diagnostics_out is not None:
            _write_json(diagnostics_out, diagnostics)
            diagnostics["diagnostics_out"] = str(diagnostics_out)
        if sensitivity_report_out is not None:
            diagnostics["sensitivity_report_out"] = str(sensitivity_report_out)
        raise ValueError(
            "raw_input_count_below_min:"
            f"packaged_count={len(candidates)}:min_packaged={min_packaged}:"
            f"candidate_skip_counts={diagnostics['candidate_skip_counts']}:"
            f"family_skip_counts={diagnostics['family_skip_counts']}:"
            f"row_skip_counts={diagnostics['row_skip_counts']}:"
            f"diagnostics_out={diagnostics.get('diagnostics_out')}"
        )

    payload = {
        "schema": RAW_SCHEMA,
        "screening_artifact": source_path,
        "screening_artifact_hash": artifact.get("screening_artifact_hash"),
        "feature_recipe_hash_policy": FEATURE_RECIPE_HASH_POLICY,
        "candidates": candidates,
        "diagnostics": {
            "family_count": len(family_rows),
            "accepted_family_count": len(family_payloads),
            "row_skip_reasons": dict(row_skip_reasons),
            "family_skips": family_skips,
            "candidate_skips": candidate_skips,
        },
    }
    _write_json(out_path, payload)
    persisted = _load_json_object(out_path, "written_raw_robustness_inputs")
    return {
        "status": "ok",
        "out": str(out_path),
        "packaged_count": len(candidates),
        "packaged_candidate_ids": sorted(persisted.get("candidates", {}).keys()),
        "skipped": {
            "rows": dict(row_skip_reasons),
            "families": family_skips,
            "candidates": candidate_skips,
        },
    }


def _fee_model_stress_inputs(symbol: str) -> tuple[float, float, str] | None:
    """Derive (fee_per_round_trip, tick_value, product) from FeeModel.

    Returns None when the product is not explicitly covered by both the fee
    schedule and the tick-value table — FeeModel's silent 1.25/12.50 fallbacks
    for unknown products must not leak into stress evidence.
    """
    from backtest_pipeline.src.fee_model import FeeModel

    product = str(symbol).split(".")[0].upper()
    model = FeeModel(product=product)
    if product not in model.fees or product not in FeeModel.TICK_VALUES:
        return None
    # Round trip = 2 sides, all-in (exchange + clearing + broker + NFA).
    return 2.0 * model.get_fee_per_contract(), FeeModel.TICK_VALUES[product], product


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build hft3 raw robustness inputs from complete VectorBT screening surfaces.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--screening-artifact", type=Path)
    source.add_argument(
        "--screening-artifact-dir",
        type=Path,
        help=(
            "Directory containing per-unit screening_artifact.json files. "
            "Directory mode is diagnostic-only and never writes raw replay inputs."
        ),
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--min-events", type=int, default=4)
    parser.add_argument("--min-parameter-combinations", type=int, default=100)
    parser.add_argument("--min-completeness", type=float, default=1.0)
    parser.add_argument("--min-packaged", type=int, default=1)
    parser.add_argument("--fee-per-rt", type=float, default=None)
    parser.add_argument("--tick-value", type=float, default=None)
    parser.add_argument(
        "--fees-from-model",
        action="store_true",
        help=(
            "Derive fee-per-round-trip and tick value per candidate from "
            "FeeModel using the candidate family's product symbol. Fails "
            "closed for products outside the explicit fee/tick tables. "
            "Mutually exclusive with --fee-per-rt/--tick-value."
        ),
    )
    parser.add_argument("--diagnostics-out", type=Path, default=None)
    parser.add_argument(
        "--surface-policy",
        choices=SURFACE_POLICIES,
        default="current_first_event",
    )
    parser.add_argument(
        "--sensitivity-report-out",
        type=Path,
        nargs="?",
        const=DEFAULT_SENSITIVITY_REPORT_OUT,
        default=None,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = build_robustness_raw_inputs_from_screening(
            screening_artifact_path=args.screening_artifact,
            screening_artifact_dir=args.screening_artifact_dir,
            out_path=args.out,
            source_root=args.source_root,
            folds=args.folds,
            min_events=args.min_events,
            min_parameter_combinations=args.min_parameter_combinations,
            min_completeness=args.min_completeness,
            min_packaged=args.min_packaged,
            fee_per_rt=args.fee_per_rt,
            tick_value=args.tick_value,
            fees_from_model=args.fees_from_model,
            diagnostics_out=args.diagnostics_out,
            surface_policy=args.surface_policy,
            sensitivity_report_out=args.sensitivity_report_out,
        )
    except (ScreeningArtifactError, ValueError) as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed.
        return _error("build_robustness_raw_inputs_failed", detail=str(exc))
    print(_compact_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
