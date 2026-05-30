"""Tests for workbench UI flow_state helpers."""

from __future__ import annotations

import json
from pathlib import Path


def test_pick_latest_event_with_aar(tmp_path: Path) -> None:
    from workbench.ui.flow_state import pick_first_event_with_aar, pick_latest_event_with_aar

    base = tmp_path / "research_cards" / "workbench_runs" / "HYP_5_MES_v_0_20260101T000000Z"
    ev1 = base / "periods" / "P1" / "events" / "EVT_A"
    ev2 = base / "periods" / "P1" / "events" / "EVT_B"
    ev1.mkdir(parents=True)
    ev2.mkdir(parents=True)
    import os
    import time

    (ev1 / "diagnostics.json").write_text("{}", encoding="utf-8")
    (ev2 / "after_action_symbolic.json").write_text('{"passed": true}', encoding="utf-8")
    time.sleep(0.01)
    os.utime(ev2 / "after_action_symbolic.json", None)

    p1, e1 = pick_first_event_with_aar(tmp_path, base.name)
    assert p1 == "P1"
    assert e1 == "EVT_A"

    p2, e2 = pick_latest_event_with_aar(tmp_path, base.name)
    assert p2 == "P1"
    assert e2 == "EVT_B"


def test_poll_campaign_status_missing(tmp_path: Path) -> None:
    from workbench.ui.flow_state import poll_campaign_status

    status = poll_campaign_status(tmp_path, "")
    assert status["state"] == "idle"

    cid = "test_campaign"
    job = tmp_path / "research_cards" / "workbench_runs" / cid
    job.mkdir(parents=True)
    (job / "status.json").write_text(
        json.dumps({"state": "running", "period": "P1", "event_id": "EVT_X"}),
        encoding="utf-8",
    )
    out = poll_campaign_status(tmp_path, cid)
    assert out["state"] == "running"
    assert out["event_id"] == "EVT_X"


def test_start_campaign_clears_drill_down_keys(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from workbench.ui import flow_state

    class FakeSessionState(dict):
        def __getattr__(self, name: str):
            return self.get(name)

        def __setattr__(self, name: str, value) -> None:
            self[name] = value

    state = FakeSessionState(
        wb_proc=None,
        wb_active_campaign="old",
        wb_selected_model="",
        wb_symbol="",
        wb_campaign_state="",
        wb_auto_period="P1",
        wb_auto_event="EVT_OLD",
        wb__period_sel="stale_period",
        wb__event_sel="stale_event",
        wb_chat_messages=[{"role": "user", "content": "hi"}],
        wb_chat_context_key="old:ctx",
        wb_ui_tab="Model Selector",
    )
    monkeypatch.setattr(flow_state.st, "session_state", state)
    monkeypatch.setattr(
        flow_state,
        "start_campaign_subprocess",
        lambda *a, **k: (SimpleNamespace(), "new_campaign"),
    )
    monkeypatch.setattr(flow_state, "set_control", lambda *a, **k: None)

    cid = flow_state.start_campaign_for_selection(
        tmp_path,
        model_id="HYP_5",
        symbol="MES.v.0",
        composition=None,
    )
    assert cid == "new_campaign"
    assert state["wb__period_sel"] == ""
    assert state["wb__event_sel"] == ""
    assert state.get("wb_auto_period") == ""
    assert state.get("wb_auto_event") == ""
    assert state.get("wb_chat_messages") == []
    assert state.get("wb_ui_tab") == "Backtest Results"


def test_workflow_tabs_order() -> None:
    from workbench.ui.workflow_tabs import WORKFLOW_TABS

    assert WORKFLOW_TABS[0] == "Model Selector"
    assert WORKFLOW_TABS[1] == "Backtest Results"
    assert WORKFLOW_TABS.index("Personal Runs") == len(WORKFLOW_TABS) - 1


def test_navigate_to_tab(monkeypatch) -> None:
    from workbench.ui import flow_state

    class FakeSessionState(dict):
        def __getattr__(self, name: str):
            return self.get(name)

        def __setattr__(self, name: str, value) -> None:
            self[name] = value

    state = FakeSessionState(wb_ui_tab="Model Selector", wb_nav_hint="")
    monkeypatch.setattr(flow_state.st, "session_state", state)
    flow_state.navigate_to_tab("Robustness")
    assert state["wb_ui_tab"] == "Robustness"
    assert "Robustness" in state["wb_nav_hint"]
    flow_state.navigate_to_tab("invalid")
    assert state["wb_ui_tab"] == "Robustness"


def test_resolve_period_event_ignores_manual_while_running(tmp_path: Path, monkeypatch) -> None:
    import json

    from workbench.ui import flow_state

    class FakeSessionState(dict):
        def __getattr__(self, name: str):
            return self.get(name)

        def __setattr__(self, name: str, value) -> None:
            self[name] = value

    cid = "camp_running"
    job = tmp_path / "research_cards" / "workbench_runs" / cid
    ev = job / "periods" / "P1" / "events" / "EVT_DONE"
    ev.mkdir(parents=True)
    (ev / "diagnostics.json").write_text("{}", encoding="utf-8")
    (job / "status.json").write_text(json.dumps({"state": "running"}), encoding="utf-8")

    state = FakeSessionState(
        wb__period_sel="manual_period",
        wb__event_sel="manual_event",
        wb_auto_period="",
        wb_auto_event="",
    )
    monkeypatch.setattr(flow_state.st, "session_state", state)

    period, event_id = flow_state.resolve_period_event(tmp_path, cid)
    assert period == ""
    assert event_id == ""
