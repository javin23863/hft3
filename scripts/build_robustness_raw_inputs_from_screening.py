#!/usr/bin/env python3
"""Build raw robustness inputs from a measured VectorBT screening artifact.

This is an assembler, not a robustness calculator. It only packages measured
parameter-surface rows that already exist in a screening artifact and fails
closed when the family surface is incomplete.
"""
from __future__ import annotations

import argparse
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
FEATURE_RECIPE_HASH_POLICY = "event_specific_hash_bound_per_candidate"
EVENT_DATE_RE = re.compile(r"(20\d{2})_(\d{2})_(\d{2})")
ROBUSTNESS_SCOPE = "assembled_screening_surface_evidence"


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


def _build_family_payload(
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
    surface = _compute_surface(rows_by_pair, events, params)
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
            "missing_profit_factor_count": sum(1 for row in family_rows if row.profit_factor_missing),
        },
        "ok",
    )


def build_robustness_raw_inputs_from_screening(
    *,
    screening_artifact_path: Path,
    out_path: Path,
    source_root: Path | None,
    folds: int,
    min_events: int,
    min_parameter_combinations: int,
    min_completeness: float,
    min_packaged: int,
    fee_per_rt: float | None,
    tick_value: float | None,
    diagnostics_out: Path | None = None,
) -> dict[str, Any]:
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
    if screening_artifact_path.resolve() == out_path.resolve():
        raise ValueError("out_must_not_overwrite_screening_artifact")
    artifact = _load_json_object(screening_artifact_path, "screening_artifact")
    validate_screening_artifact(artifact)

    promoted_rows = [row for row in artifact.get("promoted", []) if isinstance(row, Mapping)]
    rejected_rows = [row for row in artifact.get("rejected", []) if isinstance(row, Mapping)]
    promoted_by_id = {str(row.get("candidate_id")): row for row in promoted_rows if row.get("candidate_id")}

    family_rows: dict[tuple[str, str, str, str, str], list[MeasuredRow]] = defaultdict(list)
    row_skip_reasons: Counter[str] = Counter()
    for row in promoted_rows + rejected_rows:
        measured, reason = _extract_measured_row(row)
        if measured is None:
            if reason == "family_key_missing":
                candidate_id = str(row.get("candidate_id") or "unknown_candidate")
                raise ValueError(f"family_key_missing:{candidate_id}")
            row_skip_reasons[str(reason or "row_unusable")] += 1
            continue
        family_rows[measured.family_key].append(measured)

    promoted_measured: dict[str, MeasuredRow] = {}
    for row in promoted_rows:
        measured, _reason = _extract_measured_row(row)
        if measured is not None:
            promoted_measured[measured.candidate_id] = measured

    family_payloads: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    family_skips: dict[str, str] = {}
    for key, rows in sorted(family_rows.items(), key=lambda item: item[0]):
        payload, reason = _build_family_payload(
            family_rows=rows,
            folds_requested=folds,
            min_events=min_events,
            min_parameter_combinations=min_parameter_combinations,
            min_completeness=min_completeness,
        )
        if payload is None:
            family_skips[_compact_json(dict(zip(("model_id", "symbol", "event_type", "research_clock", "context_set_id"), key)))] = reason
            continue
        family_payloads[key] = payload

    source_path = _source_path(screening_artifact_path, source_root)
    candidates: dict[str, Any] = {}
    candidate_skips: dict[str, str] = {}
    stress_ready = fee_per_rt is not None and tick_value is not None
    for candidate_id, measured in sorted(promoted_measured.items()):
        if candidate_id not in promoted_by_id:
            continue
        if not stress_ready:
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
                "per_event_fee_per_rt": [float(fee_per_rt)] * n_events,
                "per_event_tick_value": [float(tick_value)] * n_events,
                "p_values": family["p_values"],
            },
            "diagnostics": {
                "event_count": n_events,
                "parameter_combination_count": family["n_trials"],
                "missing_profit_factor_count": family["missing_profit_factor_count"],
            },
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build hft3 raw robustness inputs from complete VectorBT screening surfaces.",
    )
    parser.add_argument("--screening-artifact", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--min-events", type=int, default=4)
    parser.add_argument("--min-parameter-combinations", type=int, default=100)
    parser.add_argument("--min-completeness", type=float, default=1.0)
    parser.add_argument("--min-packaged", type=int, default=1)
    parser.add_argument("--fee-per-rt", type=float, default=None)
    parser.add_argument("--tick-value", type=float, default=None)
    parser.add_argument("--diagnostics-out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = build_robustness_raw_inputs_from_screening(
            screening_artifact_path=args.screening_artifact,
            out_path=args.out,
            source_root=args.source_root,
            folds=args.folds,
            min_events=args.min_events,
            min_parameter_combinations=args.min_parameter_combinations,
            min_completeness=args.min_completeness,
            min_packaged=args.min_packaged,
            fee_per_rt=args.fee_per_rt,
            tick_value=args.tick_value,
            diagnostics_out=args.diagnostics_out,
        )
    except (ScreeningArtifactError, ValueError) as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail closed.
        return _error("build_robustness_raw_inputs_failed", detail=str(exc))
    print(_compact_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
