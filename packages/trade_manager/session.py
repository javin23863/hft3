from __future__ import annotations

import dataclasses
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


JSON_OBJECT_ARTIFACTS = (
    "session_manifest.json",
    "active_models.json",
    "registry_references.json",
    "risk_limits.json",
    "latency_metrics.json",
    "slippage_metrics.json",
    "session_metrics.json",
)

JSONL_OBJECT_ARTIFACTS = (
    "order_intents.jsonl",
    "order_state_transitions.jsonl",
    "risk_rejections.jsonl",
    "fills.jsonl",
    "positions.jsonl",
    "pnl_timeseries.jsonl",
    "incident_log.jsonl",
    "kill_switch_events.jsonl",
)

SESSION_ARTIFACTS = JSON_OBJECT_ARTIFACTS + JSONL_OBJECT_ARTIFACTS + ("session_report.md",)


class SessionReportError(ValueError):
    """Raised when Phase 23 session artifacts are unsafe or malformed."""


@dataclass(frozen=True)
class SessionArtifacts:
    session_id: str
    session_path: str
    artifacts: tuple[str, ...] = SESSION_ARTIFACTS

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "session_path": self.session_path, "artifacts": list(self.artifacts)}


@dataclass(frozen=True)
class SessionReportInput:
    session_id: str
    session_manifest: Any | None = None
    active_models: Any | None = None
    registry_references: Any | None = None
    risk_limits: Any | None = None
    order_intents: Iterable[Any] | None = None
    order_state_transitions: Iterable[Any] | None = None
    risk_rejections: Iterable[Any] | None = None
    fills: Iterable[Any] | None = None
    positions: Iterable[Any] | None = None
    pnl_timeseries: Iterable[Any] | None = None
    latency_metrics: Any | None = None
    slippage_metrics: Any | None = None
    incident_log: Iterable[Any] | None = None
    kill_switch_events: Iterable[Any] | None = None
    session_metrics: Any | None = None


def write_session_report(sessions_root: Path | str, report_input: SessionReportInput | dict[str, Any]) -> SessionArtifacts:
    data = report_input if isinstance(report_input, SessionReportInput) else SessionReportInput(**report_input)
    session_path = resolve_session_path(sessions_root, data.session_id)
    objects, jsonl_records = _build_payloads(data)
    report = _render_markdown(data.session_id, objects, jsonl_records)

    session_path.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name in JSON_OBJECT_ARTIFACTS:
        _atomic_write_text(session_path / name, json.dumps(objects[name], sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        written.append(name)
    for name in JSONL_OBJECT_ARTIFACTS:
        lines = [json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) for record in jsonl_records[name]]
        _atomic_write_text(session_path / name, ("\n".join(lines) + "\n") if lines else "")
        written.append(name)
    _atomic_write_text(session_path / "session_report.md", report)
    written.append("session_report.md")

    return SessionArtifacts(data.session_id, str(session_path), tuple(written))


def resolve_session_path(sessions_root: Path | str, session_id: str) -> Path:
    _validate_session_id(session_id)
    root = Path(sessions_root).resolve()
    session_path = (root / session_id).resolve()
    if root != session_path and root not in session_path.parents:
        raise SessionReportError("SESSION_PATH_TRAVERSAL")
    return session_path


def _build_payloads(data: SessionReportInput) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], ...]]]:
    objects = {
        "session_manifest.json": _json_object(data.session_manifest, "session_manifest.json"),
        "active_models.json": _json_object(data.active_models, "active_models.json"),
        "registry_references.json": _json_object(data.registry_references, "registry_references.json"),
        "risk_limits.json": _json_object(data.risk_limits, "risk_limits.json"),
        "latency_metrics.json": _json_object(data.latency_metrics, "latency_metrics.json"),
        "slippage_metrics.json": _json_object(data.slippage_metrics, "slippage_metrics.json"),
        "session_metrics.json": _json_object(data.session_metrics, "session_metrics.json"),
    }
    jsonl_records = {
        "order_intents.jsonl": _jsonl_objects(data.order_intents, "order_intents.jsonl"),
        "order_state_transitions.jsonl": _jsonl_objects(data.order_state_transitions, "order_state_transitions.jsonl"),
        "risk_rejections.jsonl": _jsonl_objects(data.risk_rejections, "risk_rejections.jsonl"),
        "fills.jsonl": _jsonl_objects(data.fills, "fills.jsonl"),
        "positions.jsonl": _jsonl_objects(data.positions, "positions.jsonl"),
        "pnl_timeseries.jsonl": _jsonl_objects(data.pnl_timeseries, "pnl_timeseries.jsonl"),
        "incident_log.jsonl": _jsonl_objects(data.incident_log, "incident_log.jsonl"),
        "kill_switch_events.jsonl": _jsonl_objects(data.kill_switch_events, "kill_switch_events.jsonl"),
    }
    return objects, jsonl_records


def _json_object(value: Any, where: str) -> dict[str, Any]:
    value = {} if value is None else _to_json_value(value)
    if not isinstance(value, dict):
        raise SessionReportError(f"{where}: JSON_OBJECT_REQUIRED")
    _reject_non_finite(value, where)
    return value


def _jsonl_objects(values: Iterable[Any] | None, where: str) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for index, value in enumerate(values or (), start=1):
        record = _to_json_value(value)
        if not isinstance(record, dict):
            raise SessionReportError(f"{where}:{index}: JSONL_OBJECT_REQUIRED")
        _reject_non_finite(record, f"{where}:{index}")
        records.append(record)
    return tuple(records)


def _to_json_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _to_json_value(value.to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _to_json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise SessionReportError("JSON_OBJECT_KEYS_MUST_BE_STRINGS")
            converted[key] = _to_json_value(child)
        return converted
    if isinstance(value, (list, tuple)):
        return [_to_json_value(child) for child in value]
    return value


def _reject_non_finite(value: Any, where: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SessionReportError(f"{where}: NON_FINITE_NUMBER")
    if isinstance(value, dict):
        for child in value.values():
            _reject_non_finite(child, where)
    elif isinstance(value, list):
        for child in value:
            _reject_non_finite(child, where)


def _render_markdown(
    session_id: str,
    objects: dict[str, dict[str, Any]],
    jsonl_records: dict[str, tuple[dict[str, Any], ...]],
) -> str:
    metrics = objects["session_metrics.json"]
    kill_switch_events = jsonl_records["kill_switch_events.jsonl"]
    latest_kill_switch = kill_switch_events[-1] if kill_switch_events else {}
    lines = [
        f"# Session Report: {session_id}",
        "",
        "## Metrics",
        _json_summary(metrics),
        "",
        "## Artifact Counts",
    ]
    for name in JSONL_OBJECT_ARTIFACTS:
        lines.append(f"- {name}: {len(jsonl_records[name])}")
    lines.extend(
        [
            "",
            "## Kill Switch",
            _json_summary(latest_kill_switch),
            "",
        ]
    )
    return "\n".join(lines)


def _json_summary(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) if value else "{}"


def _atomic_write_text(path: Path, text: str) -> None:
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass
        raise


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id:
        raise SessionReportError("SESSION_ID_INVALID")
    path = Path(session_id)
    if path.is_absolute() or path.name != session_id or session_id in {".", ".."}:
        raise SessionReportError("SESSION_PATH_TRAVERSAL")
