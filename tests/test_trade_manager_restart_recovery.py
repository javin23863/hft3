from __future__ import annotations

import json

import pytest

from trade_manager import SessionReportInput, write_session_report
from trade_manager.restart import (
    STATUS_INCIDENT,
    STATUS_OK,
    STATUS_UNKNOWN,
    RestartRecoveryError,
    recover_trade_manager_session,
)
from trade_manager.session import SessionReportError


class _NoRouteAdapter:
    def submit_order(self, *_a, **_k):
        raise AssertionError("adapter must not be called")

    def cancel_order(self, *_a, **_k):
        raise AssertionError("adapter must not be called")


def _closed_session(tmp_path, session_id: str = "SESSION-OK") -> None:
    write_session_report(
        tmp_path,
        SessionReportInput(
            session_id=session_id,
            session_manifest={"session_id": session_id, "status": "COMPLETE"},
            order_intents=[{"order_intent_id": "intent-1", "symbol": "ES"}],
            order_state_transitions=[
                {"order_intent_id": "intent-1", "state": "CREATED", "timestamp_ns": 100},
                {"order_intent_id": "intent-1", "state": "FILLED", "timestamp_ns": 200},
            ],
            positions=[{"timestamp_ns": 200, "symbol": "ES", "quantity": 0.0}],
            kill_switch_events=[{"timestamp_ns": 300, "active": False, "status": "CLEAR"}],
            session_metrics={"status": "COMPLETE"},
        ),
    )


def test_restart_recovery_clean_closed_session_ok(tmp_path) -> None:
    _closed_session(tmp_path)
    report = recover_trade_manager_session(tmp_path, "SESSION-OK")
    assert report.status == STATUS_OK
    assert report.open_orders_unknown is False
    assert report.position_reconciliation_status == "OK"
    assert report.safe_to_resume_signals is False
    _NoRouteAdapter()  # guard symbol — recovery must not touch adapters


def test_restart_recovery_missing_positions_incident(tmp_path) -> None:
    write_session_report(
        tmp_path,
        SessionReportInput(
            session_id="SESSION-NOPOS",
            session_manifest={"session_id": "SESSION-NOPOS"},
            order_state_transitions=[
                {"order_intent_id": "intent-1", "state": "FILLED", "timestamp_ns": 100},
            ],
            positions=[],
        ),
    )
    report = recover_trade_manager_session(tmp_path, "SESSION-NOPOS")
    assert report.status == STATUS_INCIDENT
    assert "reconcile_positions" in report.required_operator_actions


def test_restart_recovery_malformed_transitions_unknown(tmp_path) -> None:
    session_path = tmp_path / "SESSION-BAD"
    session_path.mkdir()
    (session_path / "session_manifest.json").write_text('{"session_id":"SESSION-BAD"}', encoding="utf-8")
    (session_path / "order_state_transitions.jsonl").write_text("not-json\n", encoding="utf-8")
    report = recover_trade_manager_session(tmp_path, "SESSION-BAD")
    assert report.status == STATUS_UNKNOWN


def test_restart_recovery_path_traversal_rejected(tmp_path) -> None:
    with pytest.raises(RestartRecoveryError):
        recover_trade_manager_session(tmp_path, "..")
    with pytest.raises(SessionReportError):
        write_session_report(tmp_path, SessionReportInput(session_id=".."))


def test_restart_recovery_open_order_incident(tmp_path) -> None:
    write_session_report(
        tmp_path,
        SessionReportInput(
            session_id="SESSION-OPEN",
            session_manifest={"session_id": "SESSION-OPEN"},
            order_state_transitions=[
                {"order_intent_id": "intent-1", "state": "RISK_APPROVED", "timestamp_ns": 100},
            ],
            positions=[{"timestamp_ns": 100, "symbol": "ES", "quantity": 1.0}],
        ),
    )
    report = recover_trade_manager_session(tmp_path, "SESSION-OPEN")
    assert report.status == STATUS_INCIDENT
    assert report.open_orders_unknown is True
    assert "reconcile_open_orders" in report.required_operator_actions


def test_restart_recovery_lifecycle_registry_check(tmp_path) -> None:
    _closed_session(tmp_path, "SESSION-LC")
    lc = tmp_path / "lifecycle"
    lc.mkdir()
    (lc / "model_lifecycle.json").write_text(json.dumps({"models": {}}), encoding="utf-8")
    report = recover_trade_manager_session(tmp_path, "SESSION-LC", lifecycle_dir=lc)
    assert report.lifecycle_registry_ok is True


def test_restart_recovery_replay_mode_safe_signals(tmp_path) -> None:
    _closed_session(tmp_path)
    report = recover_trade_manager_session(tmp_path, "SESSION-OK", workstation_mode=False)
    assert report.status == STATUS_OK
    assert report.safe_to_resume_signals is True


def test_restart_recovery_missing_transitions_incident(tmp_path) -> None:
    _closed_session(tmp_path, "SESSION-NOTRANS")
    (tmp_path / "SESSION-NOTRANS" / "order_state_transitions.jsonl").unlink()
    report = recover_trade_manager_session(tmp_path, "SESSION-NOTRANS")
    assert report.status == STATUS_INCIDENT
    assert report.status != STATUS_OK
    assert report.open_orders_unknown is True
    assert "reconcile_open_orders" in report.required_operator_actions
    assert "order_state_transitions_missing" in report.notes


def test_restart_recovery_missing_kill_switch_file_incident(tmp_path) -> None:
    _closed_session(tmp_path, "SESSION-NOKILL")
    (tmp_path / "SESSION-NOKILL" / "kill_switch_events.jsonl").unlink()
    report = recover_trade_manager_session(tmp_path, "SESSION-NOKILL")
    assert report.status == STATUS_INCIDENT
    assert "review_kill_switch" in report.required_operator_actions
    assert "kill_switch_events_missing" in report.notes


def test_restart_recovery_unparseable_kill_switch_incident(tmp_path) -> None:
    _closed_session(tmp_path, "SESSION-KILL")
    session_path = tmp_path / "SESSION-KILL"
    (session_path / "kill_switch_events.jsonl").write_text("not-json\n", encoding="utf-8")
    report = recover_trade_manager_session(tmp_path, "SESSION-KILL")
    assert report.status == STATUS_INCIDENT
    assert "review_kill_switch" in report.required_operator_actions
    assert report.safe_to_resume_signals is False
