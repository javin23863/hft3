from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

EXPECTED_ARTIFACTS = JSON_OBJECT_ARTIFACTS + JSONL_OBJECT_ARTIFACTS + ("session_report.md",)


class ArtifactLoadError(ValueError):
    """Raised when observer artifacts are unsafe or malformed."""


@dataclass(frozen=True)
class ObserverView:
    session_id: str
    session_path: str
    unavailable_artifacts: tuple[str, ...]
    active_models: tuple[dict[str, Any], ...]
    symbols: tuple[str, ...]
    positions: tuple[dict[str, Any], ...]
    order_states: tuple[dict[str, Any], ...]
    risk_decisions: tuple[dict[str, Any], ...]
    kill_switch_status: dict[str, Any] | None
    incidents: tuple[dict[str, Any], ...]
    latency: dict[str, Any] | None
    pnl: dict[str, Any] | None
    slippage: dict[str, Any] | None
    session_metrics: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_path": self.session_path,
            "unavailable_artifacts": list(self.unavailable_artifacts),
            "active_models": list(self.active_models),
            "symbols": list(self.symbols),
            "positions": list(self.positions),
            "order_states": list(self.order_states),
            "risk_decisions": list(self.risk_decisions),
            "kill_switch_status": self.kill_switch_status,
            "incidents": list(self.incidents),
            "latency": self.latency,
            "pnl": self.pnl,
            "slippage": self.slippage,
            "session_metrics": self.session_metrics,
        }


def load_observer_view(sessions_root: Path | str, session_id: str) -> ObserverView:
    _validate_session_id(session_id)
    root = Path(sessions_root).resolve()
    session_path = (root / session_id).resolve()
    if root != session_path and root not in session_path.parents:
        raise ArtifactLoadError("SESSION_PATH_TRAVERSAL")

    unavailable = tuple(name for name in EXPECTED_ARTIFACTS if not (session_path / name).is_file())
    objects = {name: _read_json_object(session_path / name) for name in JSON_OBJECT_ARTIFACTS if name not in unavailable}
    records = {name: _read_jsonl_objects(session_path / name) for name in JSONL_OBJECT_ARTIFACTS if name not in unavailable}

    active_models = _active_models(objects.get("active_models.json"))
    positions = tuple(records.get("positions.jsonl", ()))
    order_states = _latest_by(records.get("order_state_transitions.jsonl", ()), ("order_intent_id", "order_id"))
    risk_decisions = tuple(records.get("risk_rejections.jsonl", ()))
    kill_switch = _latest(records.get("kill_switch_events.jsonl", ()))
    pnl = _latest(records.get("pnl_timeseries.jsonl", ()))
    symbols = _symbols(active_models, positions, records.get("order_intents.jsonl", ()), records.get("fills.jsonl", ()))

    return ObserverView(
        session_id=session_id,
        session_path=str(session_path),
        unavailable_artifacts=unavailable,
        active_models=active_models,
        symbols=symbols,
        positions=positions,
        order_states=order_states,
        risk_decisions=risk_decisions,
        kill_switch_status=kill_switch,
        incidents=tuple(records.get("incident_log.jsonl", ())),
        latency=objects.get("latency_metrics.json"),
        pnl=pnl,
        slippage=objects.get("slippage_metrics.json"),
        session_metrics=objects.get("session_metrics.json"),
    )


def render_view(view: ObserverView) -> str:
    lines = [f"Observer View: {view.session_id}", f"Session Path: {view.session_path}"]
    _section(lines, "Unavailable Artifacts", view.unavailable_artifacts)
    _section(lines, "Active Models", [_brief(item, ("model_id", "status", "symbols", "allowed_symbols")) for item in view.active_models])
    _section(lines, "Symbols", view.symbols)
    _section(lines, "Positions", [_brief(item, ("timestamp_ns", "symbol", "quantity", "status", "positions")) for item in view.positions])
    _section(lines, "Order States", [_brief(item, ("order_intent_id", "order_id", "state", "timestamp_ns", "risk_reason")) for item in view.order_states])
    _section(lines, "Risk Decisions", [_brief(item, ("order_intent_id", "model_id", "symbol", "reason", "risk_reason", "status")) for item in view.risk_decisions])
    _section(lines, "Kill Switch", [_brief(view.kill_switch_status, ("timestamp_ns", "active", "status", "trigger", "requested_actions", "decision"))] if view.kill_switch_status else ())
    _section(lines, "Incidents", [_brief(item, ("timestamp_ns", "severity", "incident_id", "message", "reason")) for item in view.incidents])
    _section(lines, "Latency", [_brief(view.latency, ("p50_ns", "p95_ns", "p99_ns", "max_ns", "status"))] if view.latency else ())
    _section(lines, "Slippage", [_brief(view.slippage, ("avg_ticks", "max_ticks", "status"))] if view.slippage else ())
    _section(lines, "PnL", [_brief(view.pnl, ("timestamp_ns", "realized_pnl", "unrealized_pnl", "total_pnl", "drawdown"))] if view.pnl else ())
    _section(lines, "Session Metrics", [_brief(view.session_metrics, ("orders", "fills", "rejects", "status"))] if view.session_metrics else ())
    return "\n".join(lines)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as exc:
        raise ArtifactLoadError(f"{path.name}: READ_ERROR: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactLoadError(f"{path.name}: MALFORMED_JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise ArtifactLoadError(f"{path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactLoadError(f"{path.name}: JSON_OBJECT_REQUIRED")
    _reject_non_finite(value, path.name)
    return value


def _read_jsonl_objects(path: Path) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArtifactLoadError(f"{path.name}: READ_ERROR: {exc}") from exc
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise ArtifactLoadError(f"{path.name}:{index}: MALFORMED_JSONL: {exc.msg}") from exc
        except ValueError as exc:
            raise ArtifactLoadError(f"{path.name}:{index}: {exc}") from exc
        if not isinstance(value, dict):
            raise ArtifactLoadError(f"{path.name}:{index}: JSONL_OBJECT_REQUIRED")
        _reject_non_finite(value, f"{path.name}:{index}")
        records.append(value)
    return tuple(records)


def _reject_constant(value: str) -> None:
    raise ValueError(f"NON_FINITE_NUMBER: {value}")


def _reject_non_finite(value: Any, where: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ArtifactLoadError(f"{where}: NON_FINITE_NUMBER")
    if isinstance(value, dict):
        for child in value.values():
            _reject_non_finite(child, where)
    elif isinstance(value, list):
        for child in value:
            _reject_non_finite(child, where)


def _active_models(value: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    raw = value.get("active_models", value.get("models", []))
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, dict))


def _symbols(*groups: Any) -> tuple[str, ...]:
    found: set[str] = set()
    for group in groups:
        for item in group or ():
            if not isinstance(item, dict):
                continue
            for key in ("symbol", "instrument"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    found.add(value)
            for key in ("symbols", "allowed_symbols"):
                values = item.get(key)
                if isinstance(values, list):
                    found.update(str(value) for value in values if value)
            positions = item.get("positions")
            if isinstance(positions, dict):
                found.update(str(symbol) for symbol in positions if symbol)
    return tuple(sorted(found))


def _latest(records: tuple[dict[str, Any], ...] | None) -> dict[str, Any] | None:
    if not records:
        return None
    return sorted(records, key=_timestamp_ns)[-1]


def _latest_by(records: tuple[dict[str, Any], ...] | None, keys: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    latest: dict[str, dict[str, Any]] = {}
    for item in records or ():
        identifier = next((str(item[key]) for key in keys if key in item), "")
        if identifier and _timestamp_ns(item) >= _timestamp_ns(latest.get(identifier, {})):
            latest[identifier] = item
    return tuple(latest[key] for key in sorted(latest))


def _timestamp_ns(item: dict[str, Any]) -> int:
    value = item.get("timestamp_ns", -1)
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return -1


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not session_id:
        raise ArtifactLoadError("SESSION_ID_INVALID")
    path = Path(session_id)
    if path.is_absolute() or path.name != session_id or session_id in {".", ".."}:
        raise ArtifactLoadError("SESSION_PATH_TRAVERSAL")


def _section(lines: list[str], title: str, rows: Any) -> None:
    lines.append(f"\n{title}:")
    rows = tuple(rows)
    if not rows:
        lines.append("  UNAVAILABLE")
        return
    for row in rows:
        lines.append(f"  {row}")


def _brief(item: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
    if not item:
        return "UNAVAILABLE"
    parts = [f"{key}={item[key]}" for key in keys if key in item]
    return ", ".join(parts) if parts else json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False)
