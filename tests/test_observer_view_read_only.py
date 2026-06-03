from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from observer.cli import main
from observer.read_model import ArtifactLoadError, EXPECTED_ARTIFACTS, load_observer_view, render_view


def _write_fixture(session: Path) -> None:
    session.mkdir()
    objects = {
        "session_manifest.json": {"session_id": "S1"},
        "active_models.json": {"active_models": [{"model_id": "HYP_5", "status": "ACTIVE", "allowed_symbols": ["ES"]}]},
        "registry_references.json": {"HYP_5": "registry.jsonl#1"},
        "risk_limits.json": {"max_daily_loss": 100.0},
        "latency_metrics.json": {"p95_ns": 5000, "status": "OK"},
        "slippage_metrics.json": {"avg_ticks": 0.25, "max_ticks": 1.0},
        "session_metrics.json": {"orders": 1, "fills": 1, "status": "RUNNING"},
    }
    for name, payload in objects.items():
        (session / name).write_text(json.dumps(payload), encoding="utf-8")
    records = {
        "order_intents.jsonl": [{"order_intent_id": "OI1", "model_id": "HYP_5", "symbol": "ES"}],
        "order_state_transitions.jsonl": [{"order_intent_id": "OI1", "state": "RISK_APPROVED", "timestamp_ns": 3}],
        "risk_rejections.jsonl": [{"order_intent_id": "OI2", "reason": "KILL_SWITCH_NOT_ARMED", "status": "REJECTED"}],
        "fills.jsonl": [{"order_intent_id": "OI1", "symbol": "ES", "quantity": 1.0}],
        "positions.jsonl": [{"timestamp_ns": 4, "symbol": "ES", "quantity": 1.0, "status": "OK"}],
        "pnl_timeseries.jsonl": [{"timestamp_ns": 5, "realized_pnl": 12.5, "unrealized_pnl": 1.0, "total_pnl": 13.5}],
        "incident_log.jsonl": [{"timestamp_ns": 6, "severity": "WARN", "message": "position mismatch investigated"}],
        "kill_switch_events.jsonl": [{"timestamp_ns": 7, "active": True, "trigger": "position_mismatch", "requested_actions": ["stop_new_orders"]}],
    }
    for name, payloads in records.items():
        (session / name).write_text("\n".join(json.dumps(item) for item in payloads), encoding="utf-8")
    (session / "session_report.md").write_text("# Session S1\n", encoding="utf-8")


def test_observer_cli_render_happy_path_with_fixture_artifacts(tmp_path, capsys) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    root.mkdir()
    _write_fixture(session)

    assert main(["view", "--session-id", "S1", "--sessions-root", str(root)]) == 0
    output = capsys.readouterr().out

    assert "Observer View: S1" in output
    assert "model_id=HYP_5" in output
    assert "Symbols:\n  ES" in output
    assert "quantity=1.0" in output
    assert "state=RISK_APPROVED" in output
    assert "reason=KILL_SWITCH_NOT_ARMED" in output
    assert "trigger=position_mismatch" in output
    assert "p95_ns=5000" in output
    assert "total_pnl=13.5" in output


def test_missing_artifacts_are_explicitly_unavailable(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"S1"}', encoding="utf-8")

    view = load_observer_view(root, "S1")
    output = render_view(view)

    assert "active_models.json" in view.unavailable_artifacts
    assert len(view.unavailable_artifacts) == len(EXPECTED_ARTIFACTS) - 1
    assert "Unavailable Artifacts:" in output
    assert "Active Models:\n  UNAVAILABLE" in output


def test_malformed_and_nonfinite_artifacts_fail_closed_nonzero(tmp_path, capsys) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"bad": NaN}', encoding="utf-8")

    with pytest.raises(ArtifactLoadError, match="NON_FINITE_NUMBER"):
        load_observer_view(root, "S1")
    assert main(["view", "--session-id", "S1", "--sessions-root", str(root)]) == 1
    assert "observer load error" in capsys.readouterr().err

    (session / "session_manifest.json").write_text("{", encoding="utf-8")
    assert main(["view", "--session-id", "S1", "--sessions-root", str(root)]) == 1


def test_path_traversal_is_rejected(tmp_path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    with pytest.raises(ArtifactLoadError, match="SESSION_PATH_TRAVERSAL"):
        load_observer_view(root, "../outside")


def test_observer_does_not_write_files_or_create_new_files(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    root.mkdir()
    _write_fixture(session)
    before = sorted(path.relative_to(root) for path in root.rglob("*"))

    view = load_observer_view(root, "S1")
    render_view(view)

    after = sorted(path.relative_to(root) for path in root.rglob("*"))
    assert after == before


def test_observer_does_not_create_adapters_or_route(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def guard(name: str):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"observer must not call {name}")
        return fail

    try:
        import execution.adapter_factory as adapter_factory
        import execution.interfaces as interfaces
    except ImportError:
        adapter_factory = None
        interfaces = None
    if adapter_factory is not None:
        monkeypatch.setattr(adapter_factory, "create_adapter", guard("create_adapter"), raising=False)
    if interfaces is not None:
        for name in ("submit_order", "cancel_order", "replace_order", "flatten_positions"):
            monkeypatch.setattr(interfaces.ExecutionAdapter, name, guard(name), raising=False)

    root = tmp_path / "sessions"
    session = root / "S1"
    root.mkdir()
    _write_fixture(session)
    main(["view", "--session-id", "S1", "--sessions-root", str(root)])

    assert calls == []


def test_json_and_jsonl_records_must_be_objects(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="JSON_OBJECT_REQUIRED"):
        load_observer_view(root, "S1")

    (session / "session_manifest.json").write_text("{}", encoding="utf-8")
    (session / "order_intents.jsonl").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="JSONL_OBJECT_REQUIRED"):
        load_observer_view(root, "S1")


def test_phase20_positions_and_phase21_kill_switch_decision_display(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    root.mkdir()
    _write_fixture(session)

    output = render_view(load_observer_view(root, "S1"))

    assert "Positions:" in output
    assert "status=OK" in output
    assert "Kill Switch:" in output
    assert "active=True" in output
    assert "requested_actions=['stop_new_orders']" in output


def test_latest_records_use_numeric_timestamps_and_snapshot_symbols(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    root.mkdir()
    _write_fixture(session)
    (session / "positions.jsonl").write_text(
        json.dumps({"timestamp_ns": 10, "source": "phase20", "positions": {"ES": 1.0}, "account_state": {}}),
        encoding="utf-8",
    )
    (session / "pnl_timeseries.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp_ns": 10, "total_pnl": 10.0}),
                json.dumps({"timestamp_ns": 9, "total_pnl": 9.0}),
            ]
        ),
        encoding="utf-8",
    )

    view = load_observer_view(root, "S1")
    output = render_view(view)

    assert view.symbols == ("ES",)
    assert view.pnl == {"timestamp_ns": 10, "total_pnl": 10.0}
    assert "positions={'ES': 1.0}" in output


def test_python_module_entrypoint_exits_nonzero_on_malformed_artifact(tmp_path) -> None:
    root = tmp_path / "sessions"
    session = root / "S1"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"bad": Infinity}', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path("packages").resolve()), str(Path("apps").resolve())])

    result = subprocess.run(
        [sys.executable, "-m", "observer", "view", "--session-id", "S1", "--sessions-root", str(root)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    assert "NON_FINITE_NUMBER" in result.stderr
