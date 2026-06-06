"""Low-latency execution-path audit for Workbench runs.

The audit measures internal placement speed and external acknowledgment latency
as separate facts. Replay mode consumes captured execution boundary spans;
paper-live mode must be wired to real execution boundary events before it can
emit observed placement evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import platform
import socket
import sys
from typing import Any, Iterable, Mapping, Sequence

from tools.latency_baseline.summary import compare_to_current_baseline, stats_us


SCHEMA_VERSION = "execution_path_audit_span_v1"
SUMMARY_SCHEMA_VERSION = "execution_path_audit_summary_v1"
RUNTIME_ENV_SCHEMA_VERSION = "execution_path_audit_runtime_env_v1"

INTERNAL_PLACEMENT_METRICS = (
    "tick_to_decision_us",
    "decision_to_send_trigger_us",
    "tick_to_send_trigger_us",
    "decision_to_send_us",
    "tick_to_send_us",
    "rithmic_send_call_us",
    "cancel_to_send_us",
    "replace_to_send_us",
)
EXTERNAL_CONFIRMATION_METRICS = (
    "send_to_ack_us",
    "cancel_to_ack_us",
    "replace_to_ack_us",
)
STAGE_METRICS = (
    "decode_us",
    "features_us",
    "model_decision_us",
    "arbitration_us",
    "risk_check_us",
    "order_build_us",
    "serialization_us",
    "send_call_us",
)
ALL_METRICS = INTERNAL_PLACEMENT_METRICS + EXTERNAL_CONFIRMATION_METRICS + STAGE_METRICS
COMPARISON_THRESHOLDS = {
    "p50_us": 10.0,
    "p99_us": 15.0,
    "p99_9_us": 20.0,
    "tick_to_send_p99_9_hard_fail_pct": 25.0,
}
DEFAULT_WARN_LIMITS_US = {"tick_to_send_p50": 100.0, "tick_to_send_p99": 500.0, "tick_to_send_p99_9": 1_000.0}
NATIVE_CPP_MIN_SUBMIT_ACK_SAMPLES = 1_000
NATIVE_CPP_PROBE = "rithmic_latency_probe"
NATIVE_CPP_REQUIRED_HOST = "CHI404"

TIMESTAMP_FIELDS = (
    "market_event_received_ts",
    "decode_ready_ts",
    "features_ready_ts",
    "decision_ready_ts",
    "arbitration_ready_ts",
    "risk_check_ready_ts",
    "order_ready_ts",
    "order_send_call_ts",
    "order_send_ts",
    "order_send_return_ts",
    "ack_received_ts",
    "cancel_decision_ready_ts",
    "cancel_send_ts",
    "cancel_ack_received_ts",
    "replace_decision_ready_ts",
    "replace_send_ts",
    "replace_ack_received_ts",
)


@dataclass(frozen=True)
class AuditConfig:
    repo_root: Path
    run_id: str
    mode: str
    environment: str
    broker: str
    venue: str
    symbol: str
    exchange: str
    strategy_id: str
    model_id: str
    trade_manager_id: str
    duration_seconds: float
    samples: int | None = None
    spans_jsonl: Path | None = None
    require_low_latency_mode: bool = False
    allow_python_critical_path: bool = False


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return "lataudit-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def dated_run_dir(repo_root: Path, run_id: str, timestamp_utc: str | None = None) -> Path:
    stamp = timestamp_utc or utc_now_iso()
    return Path(repo_root) / "data" / "latency_audit" / stamp[:10] / run_id


def report_paths(repo_root: Path, run_id: str) -> tuple[Path, Path, Path]:
    root = Path(repo_root) / "reports" / "latency_audit"
    return root / f"{run_id}_summary.json", root / f"{run_id}_summary.md", root / "current_low_latency_status.json"


def current_low_latency_status_path(repo_root: Path) -> Path:
    return Path(repo_root) / "reports" / "latency_audit" / "current_low_latency_status.json"


def load_current_low_latency_status(repo_root: Path | str) -> dict[str, Any] | None:
    return _load_json(current_low_latency_status_path(Path(repo_root)))


def ensure_chi404_latency_authority(
    repo_root: Path | str,
    latency_summary_path: Path | None = None,
    *,
    min_submit_ack_samples: int = NATIVE_CPP_MIN_SUBMIT_ACK_SAMPLES,
) -> dict[str, Any] | None:
    """Promote valid CHI404 native C++ latency evidence into Workbench inputs."""

    root = Path(repo_root)
    summary_path = latency_summary_path or root / "runtime" / "latency_reports" / "latency_summary.json"
    current_status = load_current_low_latency_status(root)
    candidate = _latest_valid_native_baseline(root, min_submit_ack_samples=min_submit_ack_samples)
    if candidate is None:
        return current_status
    source_path, source_summary, validation = candidate
    return promote_native_baseline_summary(
        root,
        source_path,
        source_summary=source_summary,
        validation=validation,
        latency_summary_path=summary_path,
    )


def promote_native_baseline_summary(
    repo_root: Path | str,
    baseline_summary_path: Path,
    *,
    source_summary: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    latency_summary_path: Path | None = None,
) -> dict[str, Any]:
    """Write Workbench authority artifacts from an accepted native C++ baseline."""

    root = Path(repo_root)
    source = dict(source_summary or _load_json(Path(baseline_summary_path)) or {})
    verdict = dict(validation or validate_native_baseline_summary(source))
    if not verdict.get("accepted"):
        reasons = ", ".join(str(r) for r in verdict.get("reject_reasons") or ["invalid native baseline"])
        raise ValueError(f"native baseline rejected: {reasons}")

    metrics = source.get("metrics") if isinstance(source.get("metrics"), Mapping) else {}
    send_to_ack = dict(metrics.get("send_to_ack_us") or {})
    tick_to_send = dict(metrics.get("tick_to_send_us") or {})
    tick_to_trigger = dict(metrics.get("tick_to_send_trigger_us") or {})
    run_id = str(source.get("run_id") or Path(baseline_summary_path).stem.replace("_summary", ""))
    generated_at = utc_now_iso()

    current = {
        "schema_version": "current_low_latency_status_v1",
        "run_id": run_id,
        "status": "PASS",
        "mode": "paper-native-cpp",
        "generated_at_utc": generated_at,
        "primary_kpi": "tick_to_send_us",
        "placement_trigger_kpi": "tick_to_send_trigger_us",
        "tick_to_send_trigger_p50_us": tick_to_trigger.get("p50_us"),
        "tick_to_send_trigger_p99_us": tick_to_trigger.get("p99_us"),
        "tick_to_send_trigger_p99_9_us": tick_to_trigger.get("p99_9_us"),
        "tick_to_send_p50_us": tick_to_send.get("p50_us"),
        "tick_to_send_p99_us": tick_to_send.get("p99_us"),
        "tick_to_send_p99_9_us": tick_to_send.get("p99_9_us"),
        "send_to_ack_p50_us": send_to_ack.get("p50_us"),
        "send_to_ack_p99_us": send_to_ack.get("p99_us"),
        "send_to_ack_p99_9_us": send_to_ack.get("p99_9_us"),
        "sample_count": source.get("sample_count"),
        "submit_to_ack_sample_count": send_to_ack.get("count"),
        "expected_host": NATIVE_CPP_REQUIRED_HOST,
        "source_host": _native_baseline_host(source),
        "host_role": "colo_execution",
        "hot_path_language": "c++",
        "wrapper": "none",
        "probe": NATIVE_CPP_PROBE,
        "reason": "CHI404 native C++ rithmic_latency_probe production submit-to-ack evidence observed",
        "failures": [],
        "warnings": [],
        "optimization_status": {
            "critical_language_path": {
                "status": "active_verified",
                "active_verified": True,
                "reason": "native C++ probe, no Python wrapper",
                "evidence": source.get("broker_artifacts") or {},
            }
        },
        "metrics": metrics,
        "summary_json": str(Path(baseline_summary_path)),
        "summary_md": str(Path(baseline_summary_path).with_suffix(".md")),
        "spans_path": str(source.get("sample_path") or (source.get("broker_artifacts") or {}).get("raw_events_path") or ""),
        "runtime_env_path": "",
    }

    current_path = current_low_latency_status_path(root)
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_text(json.dumps(current, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    runtime_summary_path = latency_summary_path or root / "runtime" / "latency_reports" / "latency_summary.json"
    runtime = _load_json(runtime_summary_path) or {}
    runtime.update(
        {
            "run_id": run_id,
            "timestamp_utc": generated_at,
            "authoritative_source": "chi404_native_cpp_latency_probe",
            "order_ack_measured": True,
            "order_ack_p99_ms": float(send_to_ack["p99_us"]) / 1000.0,
            "paper_order_latency": {
                "measured": True,
                "authoritative": True,
                "paired_count": int(send_to_ack["count"]),
                "source": "chi404_native_cpp_rithmic_latency_probe",
                "measurement_tier": "native_cpp",
                "profile_path": str(Path(baseline_summary_path)),
            },
            "native_cpp_order_ack": {
                "authoritative": True,
                "source": "chi404_native_cpp_rithmic_latency_probe",
                "source_summary_json": str(Path(baseline_summary_path)),
                "source_sample_path": str(source.get("sample_path") or ""),
                "hot_path_language": "c++",
                "wrapper": "none",
                "probe": NATIVE_CPP_PROBE,
                "send_to_ack_us": send_to_ack,
                "tick_to_send_us": tick_to_send,
                "tick_to_send_trigger_us": tick_to_trigger,
            },
            "rithmic_app_latency": {
                "status": "ok",
                "reason": "paper order submit-to-ack measured by native C++ rithmic_latency_probe",
                "probe": "rithmic_gateway/tools/rithmic_latency_probe.cpp",
            },
        }
    )
    lane = runtime.get("recommended_lane")
    if isinstance(lane, dict):
        lane["order_ack_blocked"] = False
        lane["partial"] = False
        lane["note"] = "paper order submit-to-ack measured by native C++ rithmic_latency_probe"
    runtime_summary_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_summary_path.write_text(json.dumps(runtime, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return current


def validate_native_baseline_summary(
    summary: Mapping[str, Any],
    *,
    min_submit_ack_samples: int = NATIVE_CPP_MIN_SUBMIT_ACK_SAMPLES,
) -> dict[str, Any]:
    reasons: list[str] = []
    if summary.get("schema_version") != "latency_baseline_summary_v1":
        reasons.append("schema_version must be latency_baseline_summary_v1")
    broker_artifacts = summary.get("broker_artifacts")
    if not isinstance(broker_artifacts, Mapping):
        reasons.append("broker_artifacts missing")
        broker_artifacts = {}
    broker_mode = summary.get("broker_mode")
    if not isinstance(broker_mode, Mapping):
        reasons.append("broker_mode missing")
        broker_mode = {}
    if str(broker_artifacts.get("hot_path_language") or "").lower() != "c++":
        reasons.append("hot_path_language must be c++")
    if str(broker_artifacts.get("wrapper") or "").lower() != "none":
        reasons.append("wrapper must be none")
    if str(broker_artifacts.get("probe") or "") != NATIVE_CPP_PROBE:
        reasons.append(f"probe must be {NATIVE_CPP_PROBE}")
    if str(broker_mode.get("status") or "").lower() != "observed":
        reasons.append("broker_mode.status must be observed")
    if _native_baseline_host(summary) != NATIVE_CPP_REQUIRED_HOST:
        reasons.append(f"operating_profile.host must be {NATIVE_CPP_REQUIRED_HOST}")

    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), Mapping) else {}
    send_to_ack = metrics.get("send_to_ack_us") if isinstance(metrics.get("send_to_ack_us"), Mapping) else {}
    tick_to_send = metrics.get("tick_to_send_us") if isinstance(metrics.get("tick_to_send_us"), Mapping) else {}
    ack_count = int(send_to_ack.get("count") or 0)
    if ack_count < int(min_submit_ack_samples):
        reasons.append(f"send_to_ack_us.count {ack_count} < {int(min_submit_ack_samples)}")
    if not _finite_number(send_to_ack.get("p99_us")):
        reasons.append("send_to_ack_us.p99_us missing")
    if not _finite_number(tick_to_send.get("p99_us")):
        reasons.append("tick_to_send_us.p99_us missing")
    return {
        "accepted": not reasons,
        "reject_reasons": reasons,
        "run_id": summary.get("run_id"),
        "submit_to_ack_sample_count": ack_count,
    }


def _native_baseline_host(summary: Mapping[str, Any]) -> str:
    operating_profile = summary.get("operating_profile")
    if isinstance(operating_profile, Mapping):
        return str(operating_profile.get("host") or "")
    return ""


def build_span(
    *,
    run_id: str,
    mode: str,
    environment: str,
    broker: str,
    venue: str,
    symbol: str,
    exchange: str,
    strategy_id: str,
    model_id: str,
    trade_manager_id: str,
    order_action: str,
    timestamps: Mapping[str, Any],
    side: str = "",
    order_type: str = "",
    quantity: float | int | None = None,
    success: bool = True,
    reject_reason: str = "",
    critical_path_language: str = "python",
    ffi_boundary_count: int = 0,
    ipc_boundary_count: int = 0,
    allocation_count_before_send: int = 0,
    pre_send_blocking_io_count: int = 0,
    serialization_bytes: int = 0,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    raw_timestamps = _coerce_timestamps(timestamps)
    span = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp_utc": timestamp_utc or utc_now_iso(),
        "mode": mode,
        "environment": environment,
        "broker": broker,
        "venue": venue,
        "exchange": exchange,
        "symbol": symbol,
        "strategy_id": strategy_id,
        "model_id": model_id,
        "trade_manager_id": trade_manager_id,
        "order_action": order_action,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "critical_path_language": critical_path_language,
        "ffi_boundary_count": int(ffi_boundary_count),
        "ipc_boundary_count": int(ipc_boundary_count),
        "allocation_count_before_send": int(allocation_count_before_send),
        "pre_send_blocking_io_count": int(pre_send_blocking_io_count),
        "serialization_bytes": int(serialization_bytes),
        **_metric_values(raw_timestamps),
        "success": bool(success),
        "reject_reason": reject_reason,
        "raw_timestamps": raw_timestamps,
    }
    errors = validate_span(span)
    if errors:
        raise ValueError("; ".join(errors))
    return span


def validate_span(span: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(span, Mapping):
        return ["span must be a JSON object"]
    if span.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("run_id", "timestamp_utc", "mode", "symbol", "strategy_id", "order_action", "raw_timestamps"):
        if span.get(field) in (None, ""):
            errors.append(f"{field} required")
    action = str(span.get("order_action") or "")
    if action not in {"new", "cancel", "replace"}:
        errors.append("order_action must be one of new, cancel, replace")
    raw = span.get("raw_timestamps")
    if not isinstance(raw, Mapping):
        errors.append("raw_timestamps must be an object")
    else:
        for field in TIMESTAMP_FIELDS:
            if field not in raw:
                errors.append(f"raw_timestamps.{field} missing")
            elif raw[field] is not None:
                _check_non_negative_int(errors, raw[field], f"raw_timestamps.{field}")
        if action == "new":
            _require(raw, errors, "market_event_received_ts", "decision_ready_ts", "order_send_ts")
            if span.get("success") is True:
                _require(raw, errors, "ack_received_ts")
        elif action == "cancel":
            _require(raw, errors, "cancel_decision_ready_ts", "cancel_send_ts")
            if span.get("success") is True:
                _require(raw, errors, "cancel_ack_received_ts")
        elif action == "replace":
            _require(raw, errors, "replace_decision_ready_ts", "replace_send_ts")
            if span.get("success") is True:
                _require(raw, errors, "replace_ack_received_ts")
        _check_order(errors, raw, ("market_event_received_ts", "decode_ready_ts", "features_ready_ts", "decision_ready_ts"))
        _check_order(
            errors,
            raw,
            (
                "decision_ready_ts",
                "arbitration_ready_ts",
                "risk_check_ready_ts",
                "order_ready_ts",
                "order_send_call_ts",
                "order_send_ts",
                "order_send_return_ts",
                "ack_received_ts",
            ),
        )
        _check_order(errors, raw, ("cancel_decision_ready_ts", "cancel_send_ts", "cancel_ack_received_ts"))
        _check_order(errors, raw, ("replace_decision_ready_ts", "replace_send_ts", "replace_ack_received_ts"))
    for metric in ALL_METRICS:
        _check_optional_finite_non_negative(errors, span.get(metric), metric)
    for field in ("ffi_boundary_count", "ipc_boundary_count", "allocation_count_before_send", "pre_send_blocking_io_count"):
        _check_non_negative_int(errors, span.get(field), field)
    return errors


def load_spans(path: Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if not path.is_file():
        return spans
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            span = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        errors = validate_span(span)
        if errors:
            raise ValueError(f"{path}:{line_no}: invalid span: {'; '.join(errors)}")
        spans.append(span)
    return spans


def run_audit(config: AuditConfig) -> dict[str, Any]:
    runtime_env = collect_runtime_env(config)
    if config.mode == "replay":
        if config.spans_jsonl is None or not config.spans_jsonl.is_file():
            return write_blocked_audit(
                config,
                runtime_env=runtime_env,
                blocked_reason="REPLAY_SPANS_JSONL_REQUIRED",
            )
        spans = load_spans(config.spans_jsonl)
        if not spans:
            return write_blocked_audit(
                config,
                runtime_env=runtime_env,
                blocked_reason="REPLAY_SPANS_JSONL_EMPTY",
            )
        return write_audit_outputs(config, spans, runtime_env=runtime_env)
    if config.mode == "paper-live":
        return write_blocked_audit(
            config,
            runtime_env=runtime_env,
            blocked_reason="PAPER_LIVE_REPLACED_BY_NATIVE_CPP_PROBE",
        )
    raise ValueError(f"unsupported mode: {config.mode}")


def write_audit_outputs(config: AuditConfig, spans: Sequence[Mapping[str, Any]], *, runtime_env: Mapping[str, Any]) -> dict[str, Any]:
    run_dir = dated_run_dir(config.repo_root, config.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    spans_path = run_dir / "spans.jsonl"
    runtime_path = run_dir / "runtime_env.json"
    with spans_path.open("w", encoding="utf-8") as fh:
        for span in spans:
            fh.write(json.dumps(dict(span), sort_keys=True, allow_nan=False) + "\n")
    runtime_path.write_text(json.dumps(runtime_env, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    current_before = _load_json(report_paths(config.repo_root, config.run_id)[2])
    summary = build_summary(
        list(spans),
        config=config,
        spans_path=spans_path,
        runtime_env_path=runtime_path,
        runtime_env=runtime_env,
        current_status=current_before,
    )
    write_summary_reports(summary, config.repo_root, config.run_id)
    return summary


def write_blocked_audit(config: AuditConfig, *, runtime_env: Mapping[str, Any], blocked_reason: str) -> dict[str, Any]:
    run_dir = dated_run_dir(config.repo_root, config.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    spans_path = run_dir / "spans.jsonl"
    runtime_path = run_dir / "runtime_env.json"
    spans_path.write_text("", encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime_env, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    summary = build_summary(
        [],
        config=config,
        spans_path=spans_path,
        runtime_env_path=runtime_path,
        runtime_env=runtime_env,
        current_status=_load_json(report_paths(config.repo_root, config.run_id)[2]),
        blocked_reason=blocked_reason,
    )
    write_summary_reports(summary, config.repo_root, config.run_id)
    return summary


def build_summary(
    spans: Sequence[Mapping[str, Any]],
    *,
    config: AuditConfig,
    spans_path: Path,
    runtime_env_path: Path,
    runtime_env: Mapping[str, Any],
    current_status: Mapping[str, Any] | None = None,
    blocked_reason: str = "",
) -> dict[str, Any]:
    metrics = {field: stats_us(_metric_values_from_spans(spans, field)) for field in ALL_METRICS}
    optimization_status = build_optimization_status(spans, runtime_env=runtime_env, config=config)
    gates = build_gates(
        spans,
        metrics=metrics,
        optimization_status=optimization_status,
        config=config,
        baseline_status=current_status,
        blocked_reason=blocked_reason,
    )
    status = _overall_status(gates, blocked_reason=blocked_reason)
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": config.run_id,
        "generated_at_utc": utc_now_iso(),
        "mode": config.mode,
        "status": status,
        "blocked_reason": blocked_reason,
        "sample_count": len(spans),
        "spans_path": str(spans_path),
        "runtime_env_path": str(runtime_env_path),
        "primary_kpi": "tick_to_send_us",
        "placement_trigger_kpi": "tick_to_send_trigger_us",
        "principle": "placement_trigger_and_sdk_return_are_separate_from_ack_latency",
        "environment": {
            "environment": config.environment,
            "broker": config.broker,
            "venue": config.venue,
            "exchange": config.exchange,
            "symbol": config.symbol,
            "strategy_id": config.strategy_id,
            "model_id": config.model_id,
            "trade_manager_id": config.trade_manager_id,
        },
        "metrics": metrics,
        "views": {
            "offensive": {
                field: metrics[field]
                for field in (
                    "tick_to_decision_us",
                    "decision_to_send_trigger_us",
                    "tick_to_send_trigger_us",
                    "decision_to_send_us",
                    "tick_to_send_us",
                    "rithmic_send_call_us",
                )
            },
            "defensive": {
                field: metrics[field]
                for field in ("cancel_to_send_us", "cancel_to_ack_us", "replace_to_send_us", "replace_to_ack_us")
            },
            "external_confirmation": {field: metrics[field] for field in EXTERNAL_CONFIRMATION_METRICS},
            "stage_breakdown": {field: metrics[field] for field in STAGE_METRICS},
        },
        "optimization_status": optimization_status,
        "gates": gates,
        "warnings": [gate for gate in gates if gate.get("status") == "WARN"],
        "failures": [gate for gate in gates if gate.get("status") == "FAIL"],
        "comparison": compare_status_to_baseline(
            {
                "run_id": config.run_id,
                "metrics": metrics,
            },
            current_status,
        ),
    }
    summary["current_low_latency_status"] = {
        "schema_version": "current_low_latency_status_v1",
        "run_id": config.run_id,
        "status": status,
        "mode": config.mode,
        "generated_at_utc": summary["generated_at_utc"],
        "primary_kpi": "tick_to_send_us",
        "placement_trigger_kpi": "tick_to_send_trigger_us",
        "tick_to_send_trigger_p50_us": metrics["tick_to_send_trigger_us"].get("p50_us"),
        "tick_to_send_trigger_p99_us": metrics["tick_to_send_trigger_us"].get("p99_us"),
        "tick_to_send_trigger_p99_9_us": metrics["tick_to_send_trigger_us"].get("p99_9_us"),
        "tick_to_send_p50_us": metrics["tick_to_send_us"].get("p50_us"),
        "tick_to_send_p99_us": metrics["tick_to_send_us"].get("p99_us"),
        "tick_to_send_p99_9_us": metrics["tick_to_send_us"].get("p99_9_us"),
        "optimization_status": optimization_status,
        "failures": summary["failures"],
        "warnings": summary["warnings"],
        "summary_json": str(report_paths(config.repo_root, config.run_id)[0]),
        "summary_md": str(report_paths(config.repo_root, config.run_id)[1]),
        "spans_path": str(spans_path),
        "runtime_env_path": str(runtime_env_path),
    }
    return summary


def build_optimization_status(
    spans: Sequence[Mapping[str, Any]],
    *,
    runtime_env: Mapping[str, Any],
    config: AuditConfig,
) -> dict[str, dict[str, Any]]:
    languages = {str(span.get("critical_path_language") or "unknown").lower() for span in spans}
    python_in_path = bool(not languages or "python" in languages)
    pre_send_io = sum(int(span.get("pre_send_blocking_io_count") or 0) for span in spans)
    allocations = sum(int(span.get("allocation_count_before_send") or 0) for span in spans)
    affinity = runtime_env.get("process_affinity") if isinstance(runtime_env.get("process_affinity"), Mapping) else {}
    cpu_pinned = bool(affinity.get("pinned"))
    low_latency_mode = bool(runtime_env.get("low_latency_mode", {}).get("enabled")) if isinstance(runtime_env.get("low_latency_mode"), Mapping) else False
    kernel_bypass = runtime_env.get("kernel_bypass") if isinstance(runtime_env.get("kernel_bypass"), Mapping) else {}
    nic = runtime_env.get("nic") if isinstance(runtime_env.get("nic"), Mapping) else {}
    return {
        "critical_language_path": _status(
            "failed" if config.require_low_latency_mode and python_in_path and not config.allow_python_critical_path else ("needs_work" if python_in_path else "active_verified"),
            active_verified=not python_in_path,
            reason=(
                "Python is present in the measured critical path"
                if python_in_path
                else "Measured critical path does not include Python spans"
            ),
            evidence={"languages": sorted(languages), "allow_python_critical_path": config.allow_python_critical_path},
        ),
        "kernel_bypass_network_path": _status(
            "active_verified" if kernel_bypass.get("active_verified") else "unknown",
            active_verified=bool(kernel_bypass.get("active_verified")),
            reason=str(kernel_bypass.get("reason") or "kernel-bypass packet path was not verified by this run"),
            evidence={"interface": nic.get("interface"), "mechanism": kernel_bypass.get("mechanism")},
        ),
        "cpu_pinning": _status(
            "active_verified" if cpu_pinned else "needs_work",
            active_verified=cpu_pinned,
            reason="process affinity is narrower than total CPU count" if cpu_pinned else "process affinity is not pinned or cannot be verified",
            evidence=affinity,
        ),
        "numa_locality": _status(
            "unknown",
            active_verified=False,
            reason="NIC NUMA node and memory locality were not verified by this Python audit",
            evidence=runtime_env.get("numa") or {},
        ),
        "locking_and_contention": _status(
            "unknown",
            active_verified=False,
            reason="mutex and queue wait instrumentation is not observed in this run",
            evidence={"spans_observed": len(spans)},
        ),
        "memory_allocation": _status(
            "needs_work" if allocations else "active_verified",
            active_verified=allocations == 0,
            reason="allocations before send were observed" if allocations else "no pre-send allocations recorded",
            evidence={"allocation_count_before_send": allocations},
        ),
        "logging_and_persistence": _status(
            "failed" if pre_send_io else "active_verified",
            active_verified=pre_send_io == 0,
            reason="blocking I/O before send was observed" if pre_send_io else "no blocking I/O before send was recorded",
            evidence={"pre_send_blocking_io_count": pre_send_io},
        ),
        "serialization_and_copy_cost": _status(
            "needs_work",
            active_verified=False,
            reason="serialization bytes and timing are measured, but copy-free path is not verified",
            evidence={"serialization_bytes": sum(int(span.get("serialization_bytes") or 0) for span in spans)},
        ),
        "risk_path": _status(
            "active_verified" if spans and _metric_values_from_spans(spans, "risk_check_us") else "unknown",
            active_verified=bool(spans and _metric_values_from_spans(spans, "risk_check_us")),
            reason="risk-check stage timing recorded" if spans else "no spans observed",
            evidence={"risk_check_samples": len(_metric_values_from_spans(spans, "risk_check_us"))},
        ),
        "timestamp_probes": _status(
            "active_verified" if spans else "missing",
            active_verified=bool(spans),
            reason="monotonic timestamp probes emitted spans" if spans else "no timestamp spans emitted",
            evidence={"spans_observed": len(spans)},
        ),
        "low_latency_mode": _status(
            "active_verified" if low_latency_mode else ("failed" if config.require_low_latency_mode else "configured_not_active"),
            active_verified=low_latency_mode,
            reason="low-latency mode is enabled" if low_latency_mode else "low-latency mode is not active",
            evidence=runtime_env.get("low_latency_mode") or {},
        ),
    }


def build_gates(
    spans: Sequence[Mapping[str, Any]],
    *,
    metrics: Mapping[str, Mapping[str, Any]],
    optimization_status: Mapping[str, Mapping[str, Any]],
    config: AuditConfig,
    baseline_status: Mapping[str, Any] | None,
    blocked_reason: str = "",
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    gates.append(_gate("mode_not_blocked", "FAIL" if blocked_reason else "PASS", blocked_reason or "mode emitted observed audit spans"))
    gates.append(
        _gate(
            "placement_ack_separated",
            "PASS" if _placement_ack_separated(metrics) else "FAIL",
            "tick-to-send and send-to-ack are reported as separate metrics",
        )
    )
    tick_stats = metrics.get("tick_to_send_us") or {}
    gates.append(
        _gate(
            "tick_to_send_p50_budget",
            "WARN" if _above(tick_stats.get("p50_us"), DEFAULT_WARN_LIMITS_US["tick_to_send_p50"]) else "PASS",
            "p50 tick-to-send warning threshold is 100us",
            value=tick_stats.get("p50_us"),
            threshold=DEFAULT_WARN_LIMITS_US["tick_to_send_p50"],
        )
    )
    gates.append(
        _gate(
            "tick_to_send_p99_budget",
            "WARN" if _above(tick_stats.get("p99_us"), DEFAULT_WARN_LIMITS_US["tick_to_send_p99"]) else "PASS",
            "p99 tick-to-send warning threshold is 500us",
            value=tick_stats.get("p99_us"),
            threshold=DEFAULT_WARN_LIMITS_US["tick_to_send_p99"],
        )
    )
    gates.append(
        _gate(
            "tick_to_send_p99_9_budget",
            "WARN" if _above(tick_stats.get("p99_9_us"), DEFAULT_WARN_LIMITS_US["tick_to_send_p99_9"]) else "PASS",
            "p99.9 tick-to-send warning threshold is 1000us",
            value=tick_stats.get("p99_9_us"),
            threshold=DEFAULT_WARN_LIMITS_US["tick_to_send_p99_9"],
        )
    )
    pre_send_io_status = optimization_status.get("logging_and_persistence", {})
    gates.append(
        _gate(
            "no_sync_persistence_before_order_send",
            "PASS" if pre_send_io_status.get("status") != "failed" else "FAIL",
            str(pre_send_io_status.get("reason") or "blocking I/O before send check"),
        )
    )
    allocation_status = optimization_status.get("memory_allocation", {})
    allocation_failed = allocation_status.get("status") == "failed"
    allocation_warn = allocation_status.get("status") == "needs_work"
    gates.append(
        _gate(
            "no_hot_path_allocation_before_order_send",
            "FAIL" if allocation_failed else ("WARN" if allocation_warn else "PASS"),
            str(allocation_status.get("reason") or "pre-send allocation check"),
        )
    )
    language_status = optimization_status.get("critical_language_path", {})
    gates.append(
        _gate(
            "python_not_in_required_low_latency_path",
            "PASS" if language_status.get("status") != "failed" else "FAIL",
            str(language_status.get("reason") or "critical language path check"),
        )
    )
    low_latency_status = optimization_status.get("low_latency_mode", {})
    gates.append(
        _gate(
            "required_low_latency_mode_active",
            "PASS" if low_latency_status.get("status") != "failed" else "FAIL",
            str(low_latency_status.get("reason") or "low-latency mode check"),
        )
    )
    comparison = compare_status_to_baseline({"metrics": metrics, "run_id": config.run_id}, baseline_status)
    gates.append(
        _gate(
            "baseline_p99_9_regression",
            "FAIL" if comparison.get("hard_failures") else ("WARN" if comparison.get("warnings") else "PASS"),
            "p99.9 regression is compared against current_low_latency_status.json when present",
            evidence={"comparison_status": comparison.get("status"), "hard_failures": comparison.get("hard_failures")},
        )
    )
    return gates


def compare_status_to_baseline(summary: Mapping[str, Any], baseline_status: Mapping[str, Any] | None) -> dict[str, Any]:
    if not baseline_status:
        return {
            "baseline_present": False,
            "status": "no_baseline",
            "warnings": [],
            "hard_failures": [],
            "reason": "current_low_latency_status.json not found",
        }
    baseline_metrics = baseline_status.get("metrics")
    if not isinstance(baseline_metrics, Mapping):
        baseline_metrics = {
            "tick_to_send_us": {
                "p50_us": baseline_status.get("tick_to_send_p50_us"),
                "p99_us": baseline_status.get("tick_to_send_p99_us"),
                "p99_9_us": baseline_status.get("tick_to_send_p99_9_us"),
            }
        }
    baseline = {"run_id": baseline_status.get("run_id", ""), "metrics": baseline_metrics}
    comparison = compare_to_current_baseline(
        dict(summary),
        baseline_path=None,
        thresholds=COMPARISON_THRESHOLDS,
    )
    comparison["baseline_present"] = True
    comparison["baseline_run_id"] = baseline.get("run_id", "")
    current_metrics = summary.get("metrics") if isinstance(summary.get("metrics"), Mapping) else {}
    comparison["metrics"] = {}
    comparison["warnings"] = []
    comparison["hard_failures"] = []
    for metric, current_stats in current_metrics.items():
        base_stats = baseline_metrics.get(metric) if isinstance(baseline_metrics, Mapping) else None
        if not isinstance(base_stats, Mapping):
            continue
        metric_cmp: dict[str, Any] = {}
        for field in ("p50_us", "p99_us", "p99_9_us"):
            current = current_stats.get(field) if isinstance(current_stats, Mapping) else None
            base = base_stats.get(field)
            entry = _compare_stat(metric=metric, field=field, current_value=current, baseline_value=base)
            metric_cmp[field] = entry
            if entry["warning"]:
                comparison["warnings"].append({"metric": metric, "field": field, "percent_change": entry["percent_change"]})
            if entry["hard_fail"]:
                comparison["hard_failures"].append({"metric": metric, "field": field, "percent_change": entry["percent_change"]})
        comparison["metrics"][metric] = metric_cmp
    comparison["status"] = "fail" if comparison["hard_failures"] else ("warn" if comparison["warnings"] else "pass")
    return comparison


def write_summary_reports(summary: Mapping[str, Any], repo_root: Path, run_id: str) -> tuple[Path, Path, Path]:
    json_path, md_path, current_path = report_paths(repo_root, run_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(summary)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    current = dict(payload.get("current_low_latency_status") or {})
    current["metrics"] = payload.get("metrics") or {}
    current_path.write_text(json.dumps(current, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return json_path, md_path, current_path


def render_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Low-Latency Execution Path Audit",
        "",
        f"Run ID: `{summary.get('run_id', '')}`",
        f"Mode: `{summary.get('mode', '')}`",
        f"Status: `{summary.get('status', 'unknown')}`",
        f"Samples: `{summary.get('sample_count', 0)}`",
        "",
        "Primary strict KPI: `tick_to_send_us`.",
        "",
        "Placement trigger KPI: `tick_to_send_trigger_us`.",
        "",
        "`tick_to_send_trigger_us` is call-entry timing. `tick_to_send_us` runs through native SDK call return. "
        "`send_to_ack_us`, `cancel_to_ack_us`, and `replace_to_ack_us` are external confirmation latency.",
        "",
    ]
    if summary.get("blocked_reason"):
        lines.extend(["## Blocker", "", f"`{summary.get('blocked_reason')}`", ""])
    for title, key in (
        ("Offensive Placement", "offensive"),
        ("Defensive Actions", "defensive"),
        ("External Confirmation", "external_confirmation"),
        ("Stage Breakdown", "stage_breakdown"),
    ):
        view = (summary.get("views") or {}).get(key) if isinstance(summary.get("views"), Mapping) else {}
        lines.extend([f"## {title}", "", _render_metric_table(view if isinstance(view, Mapping) else {}), ""])
    lines.extend(["## Optimization Status", "", _render_status_table(summary.get("optimization_status") or {}), ""])
    failures = summary.get("failures") or []
    warnings = summary.get("warnings") or []
    if failures:
        lines.extend(["## Failures", ""])
        lines.extend(f"- `{row.get('gate')}`: {row.get('reason')}" for row in failures if isinstance(row, Mapping))
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- `{row.get('gate')}`: {row.get('reason')}" for row in warnings if isinstance(row, Mapping))
        lines.append("")
    return "\n".join(lines)


def collect_runtime_env(config: AuditConfig) -> dict[str, Any]:
    cpu_count = os.cpu_count()
    affinity_cpus = _process_affinity()
    low_latency_enabled = _env_bool("HFT3_LOW_LATENCY_MODE")
    return {
        "schema_version": RUNTIME_ENV_SCHEMA_VERSION,
        "captured_at_utc": utc_now_iso(),
        "host": socket.gethostname(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        },
        "cpu": {
            "logical_cpu_count": cpu_count,
        },
        "process_affinity": {
            "cpus": affinity_cpus,
            "cpu_count": len(affinity_cpus) if affinity_cpus is not None else None,
            "pinned": bool(cpu_count and affinity_cpus is not None and len(affinity_cpus) < cpu_count),
        },
        "numa": {
            "status": "unknown",
            "reason": "NUMA locality requires OS/NIC evidence outside this Python audit",
        },
        "nic": {
            "interface": os.environ.get("HFT3_NIC_INTERFACE", ""),
            "driver": "",
            "coalescing": "unknown",
            "irq_affinity": "unknown",
        },
        "kernel_bypass": {
            "active_verified": False,
            "mechanism": os.environ.get("HFT3_KERNEL_BYPASS", ""),
            "reason": "no runtime packet-path proof captured",
        },
        "hugepages": {
            "status": "unknown",
            "reason": "hugepage allocation not measured by this audit",
        },
        "low_latency_mode": {
            "enabled": low_latency_enabled,
            "required": bool(config.require_low_latency_mode),
            "allow_python_critical_path": bool(config.allow_python_critical_path),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit execution-path placement speed separately from acknowledgment latency.")
    parser.add_argument("--mode", choices=["replay", "paper-live"], default="replay")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--spans-jsonl", default="", help="Captured execution boundary spans JSONL for replay mode.")
    parser.add_argument("--env", default="paper", dest="environment")
    parser.add_argument("--broker", default="rithmic")
    parser.add_argument("--venue", default="")
    parser.add_argument("--exchange", default="")
    parser.add_argument("--symbol", default="ES")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--strategy", default="latency_probe", dest="strategy_id")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--trade-manager-id", default="")
    parser.add_argument("--require-low-latency-mode", action="store_true")
    parser.add_argument("--allow-python-critical-path", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    run_id = args.run_id or default_run_id()
    venue = args.venue or args.exchange or args.broker
    config = AuditConfig(
        repo_root=repo_root,
        run_id=run_id,
        mode=args.mode,
        environment=args.environment,
        broker=args.broker,
        venue=venue,
        exchange=args.exchange or venue,
        symbol=args.symbol,
        strategy_id=args.strategy_id,
        model_id=args.model_id or f"{args.mode}_model",
        trade_manager_id=args.trade_manager_id or f"{args.mode}_trade_manager",
        duration_seconds=args.duration,
        samples=args.samples,
        spans_jsonl=Path(args.spans_jsonl).resolve() if args.spans_jsonl else None,
        require_low_latency_mode=args.require_low_latency_mode,
        allow_python_critical_path=args.allow_python_critical_path,
    )
    summary = run_audit(config)
    json_path, md_path, current_path = report_paths(repo_root, run_id)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": summary.get("status"),
                "summary_json": str(json_path),
                "summary_md": str(md_path),
                "current_low_latency_status": str(current_path),
                "spans_path": summary.get("spans_path"),
                "runtime_env_path": summary.get("runtime_env_path"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 2 if summary.get("status") == "blocked" else 0


def _coerce_timestamps(raw: Mapping[str, Any]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for field in TIMESTAMP_FIELDS:
        value = raw.get(field)
        out[field] = None if value is None else int(value)
    if out["order_send_call_ts"] is None and out["order_send_ts"] is not None:
        out["order_send_call_ts"] = out["order_send_ts"]
    if out["order_send_return_ts"] is None and out["order_send_ts"] is not None:
        out["order_send_return_ts"] = out["order_send_ts"]
    return out


def _metric_values(raw: Mapping[str, int | None]) -> dict[str, float | None]:
    return {
        "tick_to_decision_us": _duration_us(raw.get("market_event_received_ts"), raw.get("decision_ready_ts")),
        "decision_to_send_us": _duration_us(raw.get("decision_ready_ts"), raw.get("order_send_ts")),
        "decision_to_send_trigger_us": _duration_us(raw.get("decision_ready_ts"), raw.get("order_send_call_ts")),
        "tick_to_send_trigger_us": _duration_us(raw.get("market_event_received_ts"), raw.get("order_send_call_ts")),
        "tick_to_send_us": _duration_us(raw.get("market_event_received_ts"), raw.get("order_send_ts")),
        "rithmic_send_call_us": _duration_us(raw.get("order_send_call_ts"), raw.get("order_send_ts")),
        "send_to_ack_us": _duration_us(raw.get("order_send_ts"), raw.get("ack_received_ts")),
        "cancel_to_send_us": _duration_us(raw.get("cancel_decision_ready_ts"), raw.get("cancel_send_ts")),
        "cancel_to_ack_us": _duration_us(raw.get("cancel_send_ts"), raw.get("cancel_ack_received_ts")),
        "replace_to_send_us": _duration_us(raw.get("replace_decision_ready_ts"), raw.get("replace_send_ts")),
        "replace_to_ack_us": _duration_us(raw.get("replace_send_ts"), raw.get("replace_ack_received_ts")),
        "decode_us": _duration_us(raw.get("market_event_received_ts"), raw.get("decode_ready_ts")),
        "features_us": _duration_us(raw.get("decode_ready_ts"), raw.get("features_ready_ts")),
        "model_decision_us": _duration_us(raw.get("features_ready_ts"), raw.get("decision_ready_ts")),
        "arbitration_us": _duration_us(raw.get("decision_ready_ts"), raw.get("arbitration_ready_ts")),
        "risk_check_us": _duration_us(raw.get("arbitration_ready_ts"), raw.get("risk_check_ready_ts")),
        "order_build_us": _duration_us(raw.get("risk_check_ready_ts"), raw.get("order_ready_ts")),
        "serialization_us": _duration_us(raw.get("order_ready_ts"), raw.get("order_send_call_ts")),
        "send_call_us": _duration_us(raw.get("order_send_call_ts"), raw.get("order_send_ts")),
    }


def _metric_values_from_spans(spans: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for span in spans:
        value = span.get(field)
        if value is not None:
            values.append(float(value))
    return values


def _duration_us(start_ns: int | None, end_ns: int | None) -> float | None:
    if start_ns is None or end_ns is None:
        return None
    if end_ns < start_ns:
        return None
    return (int(end_ns) - int(start_ns)) / 1000.0


def _require(raw: Mapping[str, Any], errors: list[str], *fields: str) -> None:
    for field in fields:
        if raw.get(field) is None:
            errors.append(f"raw_timestamps.{field} required")


def _check_non_negative_int(errors: list[str], value: Any, field: str) -> None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be integer")
        return
    if parsed < 0:
        errors.append(f"{field} must be non-negative")


def _check_optional_finite_non_negative(errors: list[str], value: Any, field: str) -> None:
    if value is None:
        return
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be numeric")
        return
    if not math.isfinite(parsed):
        errors.append(f"{field} must be finite")
    elif parsed < 0:
        errors.append(f"{field} must be non-negative")


def _check_order(errors: list[str], raw: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    present = [(field, raw.get(field)) for field in fields if raw.get(field) is not None]
    for (prev_field, prev), (field, value) in zip(present, present[1:]):
        if int(value) < int(prev):
            errors.append(f"raw_timestamps.{field} before {prev_field}")


def _placement_ack_separated(metrics: Mapping[str, Mapping[str, Any]]) -> bool:
    return (
        "tick_to_send_trigger_us" in metrics
        and "tick_to_send_us" in metrics
        and "send_to_ack_us" in metrics
    )


def _above(value: Any, threshold: float) -> bool:
    return value is not None and float(value) > float(threshold)


def _status(status: str, *, active_verified: bool, reason: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {"status": status, "active_verified": bool(active_verified), "reason": reason, "evidence": dict(evidence)}


def _gate(gate: str, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"gate": gate, "status": status, "reason": reason, **extra}


def _overall_status(gates: Sequence[Mapping[str, Any]], *, blocked_reason: str = "") -> str:
    if blocked_reason:
        return "blocked"
    if any(gate.get("status") == "FAIL" for gate in gates):
        return "fail"
    if any(gate.get("status") == "WARN" for gate in gates):
        return "warn"
    return "pass"


def _compare_stat(*, metric: str, field: str, current_value: Any, baseline_value: Any) -> dict[str, Any]:
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
    status = "degradation" if absolute > 0 else ("improvement" if absolute < 0 else "unchanged")
    threshold = float(COMPARISON_THRESHOLDS[field])
    hard_threshold = float(COMPARISON_THRESHOLDS["tick_to_send_p99_9_hard_fail_pct"])
    warning = bool(pct is not None and pct > threshold and status == "degradation")
    hard_fail = bool(metric == "tick_to_send_us" and field == "p99_9_us" and pct is not None and pct > hard_threshold and status == "degradation")
    return {
        "status": status,
        "current_us": current,
        "baseline_us": baseline,
        "absolute_change_us": absolute,
        "percent_change": pct,
        "warning": warning,
        "hard_fail": hard_fail,
    }


def _render_metric_table(view: Mapping[str, Mapping[str, Any]]) -> str:
    headers = ["metric", "count", "min", "mean", "p50", "p90", "p95", "p99", "p99.9", "max"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for metric, stats in view.items():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(metric),
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
    return "\n".join(lines)


def _render_status_table(statuses: Mapping[str, Any]) -> str:
    lines = ["| optimization | status | reason |", "| --- | --- | --- |"]
    for name, row in statuses.items():
        payload = row if isinstance(row, Mapping) else {}
        lines.append(f"| {name} | {payload.get('status', '')} | {payload.get('reason', '')} |")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}"


def _process_affinity() -> list[int] | None:
    try:
        getter = getattr(os, "sched_getaffinity")
    except AttributeError:
        return None
    try:
        return sorted(int(cpu) for cpu in getter(0))
    except OSError:
        return None


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _latest_valid_native_baseline(
    repo_root: Path,
    *,
    min_submit_ack_samples: int,
) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    reports_root = repo_root / "reports" / "latency_baselines"
    candidates: list[Path] = []
    current = reports_root / "current_baseline.json"
    if current.is_file():
        candidates.append(current)
    if reports_root.is_dir():
        summaries = sorted(
            reports_root.glob("*_summary.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(path for path in summaries if path not in candidates)
    for path in candidates:
        payload = _load_json(path)
        if payload is None:
            continue
        validation = validate_native_baseline_summary(
            payload,
            min_submit_ack_samples=min_submit_ack_samples,
        )
        if validation.get("accepted"):
            return path, payload, validation
    return None


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
