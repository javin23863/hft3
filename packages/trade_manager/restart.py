"""Inert Trade Manager restart recovery (read-only, no adapter calls)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from trade_manager.monitor import (
    POSITION_STATUS_OK,
    POSITION_STATUS_UNKNOWN,
    ExpectedPosition,
    PositionMonitorConfig,
    PositionSnapshot,
    reconcile_positions,
)
from trade_manager.order_state import TERMINAL_ORDER_STATES
from trade_manager.session import SessionReportError, resolve_session_path

STATUS_OK = "OK"
STATUS_INCIDENT = "INCIDENT_REQUIRED"
STATUS_UNKNOWN = "UNKNOWN"


class RestartRecoveryError(ValueError):
    """Unsafe session id or recovery input."""


@dataclass(frozen=True)
class RestartRecoveryReport:
    status: str
    open_orders_unknown: bool
    position_reconciliation_status: str
    lifecycle_registry_ok: bool | None
    required_operator_actions: tuple[str, ...]
    safe_to_resume_signals: bool
    session_id: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "open_orders_unknown": self.open_orders_unknown,
            "position_reconciliation_status": self.position_reconciliation_status,
            "lifecycle_registry_ok": self.lifecycle_registry_ok,
            "required_operator_actions": list(self.required_operator_actions),
            "safe_to_resume_signals": self.safe_to_resume_signals,
            "session_id": self.session_id,
            "notes": list(self.notes),
        }


def recover_trade_manager_session(
    sessions_root: Path | str,
    session_id: str,
    *,
    lifecycle_dir: Path | str | None = None,
    now_ns: int = 0,
    workstation_mode: bool = True,
) -> RestartRecoveryReport:
    """Load session artifacts and return an inert recovery verdict."""
    try:
        session_path = resolve_session_path(sessions_root, session_id)
    except SessionReportError as exc:
        raise RestartRecoveryError(str(exc)) from exc

    notes: list[str] = []
    actions: list[str] = []
    status = STATUS_OK
    open_unknown = False
    pos_status = POSITION_STATUS_OK
    lifecycle_ok: bool | None = None

    manifest = _read_json(session_path / "session_manifest.json")
    if manifest is None:
        return _unknown(session_id, ("session_manifest_unparseable",))

    transitions_path = session_path / "order_state_transitions.jsonl"
    if not transitions_path.is_file():
        open_unknown = True
        status = STATUS_INCIDENT
        actions.append("reconcile_open_orders")
        notes.append("order_state_transitions_missing")
    else:
        transitions = _read_jsonl(transitions_path)
        if transitions is None:
            return _unknown(session_id, ("order_state_transitions_unparseable",))

        latest_by_intent = _latest_order_states(transitions)
        terminal_values = {state.value for state in TERMINAL_ORDER_STATES}
        open_intents = [
            intent_id
            for intent_id, state in latest_by_intent.items()
            if str(state) not in terminal_values
        ]
        if open_intents:
            open_unknown = True
            status = STATUS_INCIDENT
            actions.append("reconcile_open_orders")
            notes.append(f"non_terminal_orders={len(open_intents)}")

    positions_rows = _read_jsonl(session_path / "positions.jsonl")
    if positions_rows is None:
        pos_status = POSITION_STATUS_UNKNOWN
        status = STATUS_INCIDENT if status == STATUS_OK else status
        actions.append("reconcile_positions")
        notes.append("positions_unparseable")
    elif not positions_rows:
        pos_status = POSITION_STATUS_UNKNOWN
        status = STATUS_INCIDENT if status == STATUS_OK else status
        actions.append("reconcile_positions")
        notes.append("positions_missing")
    else:
        pos_status = _reconcile_latest_positions(positions_rows, now_ns=now_ns)

    if pos_status == POSITION_STATUS_UNKNOWN:
        status = STATUS_INCIDENT if status == STATUS_OK else status
        if "reconcile_positions" not in actions:
            actions.append("reconcile_positions")
    elif pos_status == "MISMATCH":
        status = STATUS_INCIDENT
        if "reconcile_positions" not in actions:
            actions.append("reconcile_positions")

    kill_path = session_path / "kill_switch_events.jsonl"
    if not kill_path.is_file():
        if status == STATUS_OK:
            status = STATUS_INCIDENT
        if "review_kill_switch" not in actions:
            actions.append("review_kill_switch")
        notes.append("kill_switch_events_missing")
    else:
        kill_rows = _read_jsonl(kill_path)
        if kill_rows is None:
            if status == STATUS_OK:
                status = STATUS_INCIDENT
            if "review_kill_switch" not in actions:
                actions.append("review_kill_switch")
            notes.append("kill_switch_events_unparseable")
        elif kill_rows:
            latest_kill = kill_rows[-1]
            if latest_kill.get("active") is True or latest_kill.get("status") not in (None, "CLEAR", "clear"):
                if "review_kill_switch" not in actions:
                    actions.append("review_kill_switch")
                if status == STATUS_OK:
                    status = STATUS_INCIDENT

    if lifecycle_dir is not None:
        lifecycle_ok = _lifecycle_registry_ok(lifecycle_dir)
        if lifecycle_ok is False and "review_lifecycle_registry" not in actions:
            actions.append("review_lifecycle_registry")

    if status == STATUS_OK and not open_unknown and pos_status == POSITION_STATUS_OK:
        status = STATUS_OK
    elif status == STATUS_OK:
        status = STATUS_INCIDENT

    signals_eligible = (
        status == STATUS_OK
        and not open_unknown
        and pos_status == POSITION_STATUS_OK
        and lifecycle_ok is not False
    )
    safe_signals = signals_eligible and not workstation_mode

    return RestartRecoveryReport(
        status=status,
        open_orders_unknown=open_unknown,
        position_reconciliation_status=pos_status,
        lifecycle_registry_ok=lifecycle_ok,
        required_operator_actions=tuple(dict.fromkeys(actions)),
        safe_to_resume_signals=safe_signals,
        session_id=session_id,
        notes=tuple(notes),
    )


def _unknown(session_id: str, notes: Iterable[str]) -> RestartRecoveryReport:
    return RestartRecoveryReport(
        status=STATUS_UNKNOWN,
        open_orders_unknown=True,
        position_reconciliation_status=POSITION_STATUS_UNKNOWN,
        lifecycle_registry_ok=None,
        required_operator_actions=("manual_incident_review",),
        safe_to_resume_signals=False,
        session_id=session_id,
        notes=tuple(notes),
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if not isinstance(rec, dict):
                return None
            rows.append(rec)
    except (OSError, json.JSONDecodeError):
        return None
    return rows


def _latest_order_states(transitions: list[dict[str, Any]]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for rec in transitions:
        intent_id = rec.get("order_intent_id")
        state = rec.get("state")
        if not intent_id or not state:
            continue
        latest[str(intent_id)] = str(state)
    return latest


def _reconcile_latest_positions(rows: list[dict[str, Any]], *, now_ns: int) -> str:
    last = rows[-1]
    ts = last.get("timestamp_ns", now_ns)
    if not isinstance(ts, int) or ts < 0:
        return POSITION_STATUS_UNKNOWN
    positions: dict[str, float] = {}
    if "symbol" in last and "quantity" in last:
        positions[str(last["symbol"])] = float(last["quantity"])
    elif "positions" in last and isinstance(last["positions"], dict):
        positions = {str(k): float(v) for k, v in last["positions"].items()}
    else:
        return POSITION_STATUS_UNKNOWN

    snapshot = PositionSnapshot(
        timestamp_ns=int(ts),
        source="session_positions",
        positions=positions,
        account_state=None,
    )
    expected = [
        ExpectedPosition(symbol=symbol, quantity=qty, source_order_intent_ids=())
        for symbol, qty in positions.items()
    ]
    result = reconcile_positions(
        timestamp_ns=int(ts),
        snapshot=snapshot,
        expected_positions=expected,
        config=PositionMonitorConfig(max_position_mismatch_contracts=0.0, stale_position_max_ns=10**18),
    )
    return result.status


def _lifecycle_registry_ok(lifecycle_dir: Path | str) -> bool:
    path = Path(lifecycle_dir) / "model_lifecycle.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("models"), dict)
