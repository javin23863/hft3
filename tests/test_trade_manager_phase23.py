from __future__ import annotations

import json
import math
from dataclasses import dataclass

import pytest

from observer.read_model import EXPECTED_ARTIFACTS, load_observer_view
from trade_manager import SESSION_ARTIFACTS, SessionReportError, SessionReportInput, write_session_report


@dataclass(frozen=True)
class _DataclassPayload:
    model_id: str
    status: str


class _ToDictPayload:
    def __init__(self) -> None:
        self.routing_calls: list[str] = []

    def to_dict(self) -> dict[str, object]:
        return {"model_id": "model-1", "status": "ACTIVE", "symbols": ["ES"]}

    def submit_order(self, order_intent):  # pragma: no cover - regression guard
        self.routing_calls.append("submit_order")
        raise AssertionError("Phase 23 session reporting must not submit orders")

    def cancel_order(self, order_id: str):  # pragma: no cover - regression guard
        self.routing_calls.append("cancel_order")
        raise AssertionError("Phase 23 session reporting must not cancel orders")

    def replace_order(self, order_id: str, new_order_intent):  # pragma: no cover - regression guard
        self.routing_calls.append("replace_order")
        raise AssertionError("Phase 23 session reporting must not replace orders")

    def flatten_positions(self):  # pragma: no cover - regression guard
        self.routing_calls.append("flatten_positions")
        raise AssertionError("Phase 23 session reporting must not flatten positions")


def _sample_input(session_id: str = "SESSION-1") -> SessionReportInput:
    return SessionReportInput(
        session_id=session_id,
        session_manifest={"session_id": session_id, "started_at": "2026-06-03T00:00:00Z"},
        active_models={"active_models": [_ToDictPayload(), _DataclassPayload("model-2", "ACTIVE")]},
        registry_references={"records": [{"model_id": "model-1", "registry_id": "reg-1"}]},
        risk_limits={"max_daily_loss": 100.0, "symbols": ["ES"]},
        order_intents=[{"order_intent_id": "intent-1", "model_id": "model-1", "symbol": "ES"}],
        order_state_transitions=[{"order_intent_id": "intent-1", "state": "CREATED", "timestamp_ns": 100}],
        risk_rejections=[{"order_intent_id": "intent-2", "reason": "MAX_ORDER_SIZE"}],
        fills=[{"order_id": "order-1", "symbol": "ES", "quantity": 1.0}],
        positions=[{"timestamp_ns": 200, "symbol": "ES", "quantity": 1.0}],
        pnl_timeseries=[{"timestamp_ns": 300, "total_pnl": 12.5}],
        latency_metrics={"p50_ns": 1000, "status": "OK"},
        slippage_metrics={"avg_ticks": 0.25, "status": "OK"},
        incident_log=[{"timestamp_ns": 400, "severity": "INFO", "message": "unit"}],
        kill_switch_events=[{"timestamp_ns": 500, "active": False, "status": "CLEAR"}],
        session_metrics={"orders": 1, "fills": 1, "rejects": 1, "status": "COMPLETE"},
    )


def test_phase23_writes_all_16_artifacts(tmp_path) -> None:
    result = write_session_report(tmp_path, _sample_input())

    session_path = tmp_path / "SESSION-1"
    assert result.artifacts == SESSION_ARTIFACTS
    assert sorted(path.name for path in session_path.iterdir()) == sorted(SESSION_ARTIFACTS)
    assert len(SESSION_ARTIFACTS) == 16
    assert json.loads((session_path / "active_models.json").read_text(encoding="utf-8"))["active_models"][0]["symbols"] == ["ES"]


def test_phase23_observer_loads_generated_session(tmp_path) -> None:
    write_session_report(tmp_path, _sample_input())

    view = load_observer_view(tmp_path, "SESSION-1")

    assert view.unavailable_artifacts == ()
    assert set(EXPECTED_ARTIFACTS) == set(SESSION_ARTIFACTS)
    assert view.symbols == ("ES",)
    assert view.session_metrics == {"fills": 1, "orders": 1, "rejects": 1, "status": "COMPLETE"}
    assert view.kill_switch_status == {"active": False, "status": "CLEAR", "timestamp_ns": 500}


def test_phase23_path_traversal_rejected_and_no_outside_write(tmp_path) -> None:
    outside = tmp_path.parent / "evil"

    with pytest.raises(SessionReportError, match="SESSION_PATH_TRAVERSAL"):
        write_session_report(tmp_path, _sample_input(".."))
    with pytest.raises(SessionReportError, match="SESSION_PATH_TRAVERSAL"):
        write_session_report(tmp_path, _sample_input("..\\evil"))

    assert not outside.exists()


def test_phase23_rejects_recursive_nonfinite_values(tmp_path) -> None:
    bad = SessionReportInput(session_id="SESSION-1", session_metrics={"nested": {"pnl": math.inf}})

    with pytest.raises(SessionReportError, match="NON_FINITE_NUMBER"):
        write_session_report(tmp_path, bad)

    assert not (tmp_path / "SESSION-1").exists()


def test_phase23_rejects_non_object_artifacts(tmp_path) -> None:
    bad_json = SessionReportInput(session_id="SESSION-1", session_metrics=["not", "object"])
    bad_jsonl = SessionReportInput(session_id="SESSION-2", fills=[{"ok": True}, ["not", "object"]])

    with pytest.raises(SessionReportError, match="JSONL_OBJECT_REQUIRED"):
        write_session_report(tmp_path, bad_jsonl)
    with pytest.raises(SessionReportError, match="JSON_OBJECT_REQUIRED"):
        write_session_report(tmp_path, bad_json)

    assert not (tmp_path / "SESSION-1").exists()
    assert not (tmp_path / "SESSION-2").exists()


def test_phase23_rejects_non_string_json_keys(tmp_path) -> None:
    bad = SessionReportInput(session_id="SESSION-1", session_metrics={1: "collides with string keys"})

    with pytest.raises(SessionReportError, match="JSON_OBJECT_KEYS_MUST_BE_STRINGS"):
        write_session_report(tmp_path, bad)

    assert not (tmp_path / "SESSION-1").exists()


def test_phase23_atomic_failure_leaves_old_content_and_no_temp(tmp_path, monkeypatch) -> None:
    session_path = tmp_path / "SESSION-1"
    session_path.mkdir()
    target = session_path / "session_manifest.json"
    target.write_text('{"old":true}\n', encoding="utf-8")
    import trade_manager.session as session_module

    original_replace = session_module.os.replace

    def fail_first_replace(src, dst):
        if str(dst).endswith("session_manifest.json"):
            raise OSError("replace failed")
        return original_replace(src, dst)

    monkeypatch.setattr(session_module.os, "replace", fail_first_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_session_report(tmp_path, _sample_input())

    assert target.read_text(encoding="utf-8") == '{"old":true}\n'
    assert list(session_path.glob("*.tmp")) == []
    assert list(session_path.glob(".*.tmp")) == []


def test_phase23_no_adapter_routing_or_flatten_calls(tmp_path) -> None:
    guard = _ToDictPayload()
    data = SessionReportInput(session_id="SESSION-1", active_models={"active_models": [guard]})

    write_session_report(tmp_path, data)

    assert guard.routing_calls == []


def test_phase23_empty_optional_streams_create_expected_defaults(tmp_path) -> None:
    write_session_report(tmp_path, SessionReportInput(session_id="SESSION-1"))
    session_path = tmp_path / "SESSION-1"

    for name in SESSION_ARTIFACTS:
        assert (session_path / name).is_file()
    for name in ("order_intents.jsonl", "fills.jsonl", "kill_switch_events.jsonl"):
        assert (session_path / name).read_text(encoding="utf-8") == ""
    assert json.loads((session_path / "session_metrics.json").read_text(encoding="utf-8")) == {}


def test_phase23_markdown_report_includes_session_metrics_and_kill_switch(tmp_path) -> None:
    write_session_report(tmp_path, _sample_input())

    report = (tmp_path / "SESSION-1" / "session_report.md").read_text(encoding="utf-8")

    assert "# Session Report: SESSION-1" in report
    assert "## Metrics" in report
    assert '"orders":1' in report
    assert "## Kill Switch" in report
    assert '"active":false' in report
