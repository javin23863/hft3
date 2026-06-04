"""Summary reports and baseline comparison for latency baseline samples."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import statistics
from pathlib import Path
import sys
from typing import Any

from .recorder import METRIC_FIELDS, load_jsonl


PRIMARY_KPI = "tick_to_send_us"
PLACEMENT_TRIGGER_KPI = "tick_to_send_trigger_us"
ROUND_TRIP_ACK_METRIC = "round_trip_ack_latency_us"
OFFENSIVE_METRICS = (
    "tick_to_decision_us",
    "decision_to_send_trigger_us",
    "tick_to_send_trigger_us",
    "decision_to_send_us",
    "tick_to_send_us",
    "rithmic_send_call_us",
)
DEFENSIVE_METRICS = (
    "cancel_to_send_us",
    "cancel_to_ack_us",
    "replace_to_send_us",
    "replace_to_ack_us",
)
ROUND_TRIP_METRICS = (
    "send_to_ack_us",
    "cancel_to_ack_us",
    "replace_to_ack_us",
    ROUND_TRIP_ACK_METRIC,
)
COMPARISON_FIELDS = ("p50_us", "p99_us", "p99_9_us")
DEFAULT_THRESHOLDS = {
    "p50_us": 10.0,
    "p99_us": 15.0,
    "p99_9_us": 20.0,
    "tick_to_send_p99_9_hard_fail_pct": 25.0,
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * pct) - 1))
    return ordered[idx]


def stats_us(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min_us": None,
            "mean_us": None,
            "p50_us": None,
            "p90_us": None,
            "p95_us": None,
            "p99_us": None,
            "p99_9_us": None,
            "max_us": None,
        }
    vals = [float(v) for v in values]
    return {
        "count": len(vals),
        "min_us": min(vals),
        "mean_us": statistics.fmean(vals),
        "p50_us": percentile(vals, 0.50),
        "p90_us": percentile(vals, 0.90),
        "p95_us": percentile(vals, 0.95),
        "p99_us": percentile(vals, 0.99),
        "p99_9_us": percentile(vals, 0.999),
        "max_us": max(vals),
    }


def _metric_values(records: list[dict[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    if metric == ROUND_TRIP_ACK_METRIC:
        for rec in records:
            for field in ("send_to_ack_us", "cancel_to_ack_us", "replace_to_ack_us"):
                value = rec.get(field)
                if value is not None:
                    values.append(float(value))
        return values
    for rec in records:
        value = rec.get(metric)
        if value is not None:
            values.append(float(value))
    return values


def build_summary(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    sample_path: Path,
    baseline_path: Path | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    metrics = {field: stats_us(_metric_values(records, field)) for field in METRIC_FIELDS}
    metrics[ROUND_TRIP_ACK_METRIC] = stats_us(_metric_values(records, ROUND_TRIP_ACK_METRIC))

    summary: dict[str, Any] = {
        "schema_version": "latency_baseline_summary_v1",
        "run_id": run_id,
        "generated_at_utc": utc_now_iso(),
        "sample_path": str(sample_path),
        "sample_count": len(records),
        "primary_kpi": PRIMARY_KPI,
        "placement_trigger_kpi": PLACEMENT_TRIGGER_KPI,
        "principle": "placement_trigger_and_sdk_return_are_separate_from_ack_latency",
        "metrics": metrics,
        "views": {
            "offensive": {field: metrics[field] for field in OFFENSIVE_METRICS},
            "defensive": {field: metrics[field] for field in DEFENSIVE_METRICS},
            "round_trip": {field: metrics[field] for field in ROUND_TRIP_METRICS},
        },
        "comparison": {},
    }
    summary["comparison"] = compare_to_current_baseline(
        summary,
        baseline_path=baseline_path,
        thresholds=thresholds or DEFAULT_THRESHOLDS,
    )
    return summary


def compare_to_current_baseline(
    summary: dict[str, Any],
    *,
    baseline_path: Path | None,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "baseline_path": str(baseline_path) if baseline_path else "",
        "baseline_present": False,
        "status": "no_baseline",
        "thresholds": thresholds,
        "metrics": {},
        "warnings": [],
        "hard_failures": [],
    }
    if baseline_path is None or not baseline_path.is_file():
        comparison["reason"] = "current_baseline.json not found"
        return comparison
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_metrics = baseline.get("metrics") or {}
    comparison["baseline_present"] = True
    comparison["baseline_run_id"] = baseline.get("run_id", "")
    current_metrics = summary.get("metrics") or {}

    for metric, current_stats in current_metrics.items():
        base_stats = baseline_metrics.get(metric) or {}
        metric_cmp: dict[str, Any] = {}
        for field in COMPARISON_FIELDS:
            current_value = current_stats.get(field)
            baseline_value = base_stats.get(field)
            metric_cmp[field] = _compare_stat(
                metric=metric,
                field=field,
                current_value=current_value,
                baseline_value=baseline_value,
                thresholds=thresholds,
            )
            entry = metric_cmp[field]
            if entry.get("warning"):
                comparison["warnings"].append(
                    {
                        "metric": metric,
                        "field": field,
                        "percent_change": entry.get("percent_change"),
                        "threshold_pct": entry.get("threshold_pct"),
                    }
                )
            if entry.get("hard_fail"):
                comparison["hard_failures"].append(
                    {
                        "metric": metric,
                        "field": field,
                        "percent_change": entry.get("percent_change"),
                        "threshold_pct": entry.get("threshold_pct"),
                    }
                )
        comparison["metrics"][metric] = metric_cmp

    if comparison["hard_failures"]:
        comparison["status"] = "fail"
    elif comparison["warnings"]:
        comparison["status"] = "warn"
    else:
        comparison["status"] = "pass"
    return comparison


def _compare_stat(
    *,
    metric: str,
    field: str,
    current_value: Any,
    baseline_value: Any,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    if current_value is None or baseline_value is None:
        return {
            "status": "insufficient",
            "current_us": current_value,
            "baseline_us": baseline_value,
            "absolute_change_us": None,
            "percent_change": None,
            "warning": False,
            "hard_fail": False,
        }
    current = float(current_value)
    baseline = float(baseline_value)
    absolute = current - baseline
    pct = None if baseline == 0 else (absolute / baseline) * 100.0
    if absolute > 0:
        status = "degradation"
    elif absolute < 0:
        status = "improvement"
    else:
        status = "unchanged"
    threshold = float(thresholds.get(field, 0.0))
    warning = bool(pct is not None and pct > threshold and status == "degradation")
    hard_threshold = float(thresholds.get("tick_to_send_p99_9_hard_fail_pct", 25.0))
    hard_fail = bool(
        metric == PRIMARY_KPI
        and field == "p99_9_us"
        and pct is not None
        and pct > hard_threshold
        and status == "degradation"
    )
    return {
        "status": status,
        "current_us": current,
        "baseline_us": baseline,
        "absolute_change_us": absolute,
        "percent_change": pct,
        "threshold_pct": hard_threshold if hard_fail else threshold,
        "warning": warning,
        "hard_fail": hard_fail,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Latency Baseline Summary",
        "",
        f"Run ID: `{summary.get('run_id', '')}`",
        f"Samples: `{summary.get('sample_count', 0)}`",
        "",
        "Primary strict KPI: `tick_to_send_us`.",
        "",
        "Placement trigger KPI: `tick_to_send_trigger_us`.",
        "",
        "`tick_to_send_trigger_us` is market callback to native send-call entry. "
        "`tick_to_send_us` is market callback through native SDK send-call return. "
        "`send_to_ack_us`, `cancel_to_ack_us`, and `replace_to_ack_us` are broker/exchange response latency.",
        "",
    ]
    for title, view in (
        ("Offensive Placement Speed", summary["views"]["offensive"]),
        ("Defensive Actions", summary["views"]["defensive"]),
        ("Round Trip Acknowledgment", summary["views"]["round_trip"]),
    ):
        lines.extend([f"## {title}", "", _render_table(view), ""])
    comparison = summary.get("comparison") or {}
    lines.extend(
        [
            "## Baseline Comparison",
            "",
            f"Status: `{comparison.get('status', 'unknown')}`",
            f"Baseline present: `{comparison.get('baseline_present', False)}`",
        ]
    )
    if comparison.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for warning in comparison["warnings"]:
            lines.append(
                f"- `{warning['metric']}` `{warning['field']}` degraded by {warning['percent_change']:.2f}%"
            )
    if comparison.get("hard_failures"):
        lines.append("")
        lines.append("Hard failures:")
        for failure in comparison["hard_failures"]:
            lines.append(
                f"- `{failure['metric']}` `{failure['field']}` degraded by {failure['percent_change']:.2f}%"
            )
    lines.append("")
    return "\n".join(lines)


def _render_table(view: dict[str, dict[str, Any]]) -> str:
    headers = ["metric", "count", "min", "mean", "p50", "p90", "p95", "p99", "p99.9", "max"]
    rows = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for metric, stats in view.items():
        rows.append(
            "| "
            + " | ".join(
                [
                    metric,
                    str(stats.get("count", 0)),
                    _fmt(stats.get("min_us")),
                    _fmt(stats.get("mean_us")),
                    _fmt(stats.get("p50_us")),
                    _fmt(stats.get("p90_us")),
                    _fmt(stats.get("p95_us")),
                    _fmt(stats.get("p99_us")),
                    _fmt(stats.get("p99_9_us")),
                    _fmt(stats.get("max_us")),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}"


def write_summary_reports(
    summary: dict[str, Any],
    *,
    reports_root: Path,
    update_current_baseline: bool = False,
) -> tuple[Path, Path, Path | None]:
    reports_root.mkdir(parents=True, exist_ok=True)
    run_id = str(summary["run_id"])
    json_path = reports_root / f"{run_id}_summary.json"
    md_path = reports_root / f"{run_id}_summary.md"
    capability_paths = _write_capability_report(summary, reports_root)
    if capability_paths is not None:
        summary["capability_report"] = {
            "json_path": str(capability_paths[0]),
            "md_path": str(capability_paths[1]),
        }
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    baseline_path: Path | None = None
    if update_current_baseline:
        baseline_path = reports_root / "current_baseline.json"
        baseline_path.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return json_path, md_path, baseline_path


def _write_capability_report(summary: dict[str, Any], reports_root: Path) -> tuple[Path, Path] | None:
    repo_root = reports_root.resolve().parents[1]
    packages_path = repo_root / "packages"
    if str(packages_path) not in sys.path:
        sys.path.insert(0, str(packages_path))
    from trade_manager.latency_capability import (
        CapabilityAssumptions,
        ModelInteractionMode,
        PendingExposureConfig,
        build_capability_report,
        write_capability_reports,
    )
    inputs = summary.get("capability_inputs") if isinstance(summary.get("capability_inputs"), dict) else {}
    pending_raw = inputs.get("pending_exposure") if isinstance(inputs.get("pending_exposure"), dict) else {}
    assumptions = CapabilityAssumptions(
        opportunity_decay_us=float(inputs.get("opportunity_decay_us", 1_000.0)),
        competitor_tick_to_send_us=(
            None if inputs.get("competitor_tick_to_send_us") is None else float(inputs.get("competitor_tick_to_send_us"))
        ),
        arbitration_latency_us=float(inputs.get("arbitration_latency_us", 0.0)),
        defensive_activation_latency_us=float(inputs.get("defensive_activation_latency_us", 0.0)),
        hybrid_coordination_latency_us=float(inputs.get("hybrid_coordination_latency_us", 0.0)),
        queue_position_penalty_us=float(inputs.get("queue_position_penalty_us", 0.0)),
        pending_exposure=PendingExposureConfig(
            max_pending_orders=int(pending_raw.get("max_pending_orders", 1)),
            max_pending_quantity=float(pending_raw.get("max_pending_quantity", 1.0)),
            max_pending_notional=float(pending_raw.get("max_pending_notional", 0.0)),
            stale_pending_timeout_us=float(pending_raw.get("stale_pending_timeout_us", 500_000.0)),
            cancel_replace_throttle_us=float(pending_raw.get("cancel_replace_throttle_us", 50_000.0)),
        ),
    )
    mode = ModelInteractionMode(str(inputs.get("model_interaction_mode", "offensive_only")))
    capability_report = build_capability_report(summary, mode=mode, assumptions=assumptions)
    return write_capability_reports(capability_report, reports_root=reports_root)


def summarize_jsonl(
    sample_path: Path,
    *,
    run_id: str,
    reports_root: Path,
    baseline_path: Path | None,
    update_current_baseline: bool = False,
) -> dict[str, Any]:
    records = load_jsonl(sample_path)
    summary = build_summary(records, run_id=run_id, sample_path=sample_path, baseline_path=baseline_path)
    write_summary_reports(summary, reports_root=reports_root, update_current_baseline=update_current_baseline)
    return summary
