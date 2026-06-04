"""Tests for analyst chat helper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from data_layer.llm.openai_compatible_client import GenerateResult


def test_chat_reply_uses_openai_compatible_client(tmp_path: Path) -> None:
    from workbench.ui.analyst_panel import _chat_reply

    art = tmp_path / "run"
    art.mkdir()
    (art / "after_action_symbolic.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    (art / "after_action_packet.json").write_text(
        json.dumps({"model_id": "HYP_5", "latency_authority": {"breakeven_us": 100}}),
        encoding="utf-8",
    )

    with patch(
        "workbench.ui.analyst_panel.llm_client.generate",
        return_value=GenerateResult("Follow-up answer.", model="test"),
    ):
        text = _chat_reply(art, "What is break-even?")
    assert "Follow-up" in text


def test_workbench_console_reply_uses_client_and_packet_context() -> None:
    from workbench.ui import analyst_panel

    snapshot = SimpleNamespace(
        source="all_lanes",
        run_id="run_001",
        state="blocked",
        current_stage="vectorbt_filter",
        data={"bitcoin_edge_packets": {"status": "OBSERVED", "observed": True, "transport": "packet"}},
        backtest={"vectorbt_summary": {"status": "BLOCKING", "observed": False, "reason": "prefilter only"}},
        diagnostics={"feature_fabric": {"status": "OBSERVED", "pit_validation_status": "PASS"}},
        decision={
            "action": "QUARANTINE",
            "live_registry_ready": False,
            "blocking_gates": [{"gate": "vectorbt_filter", "status": "PREFILTER_ONLY"}],
        },
        after_action={
            "gate_status": "FAIL",
            "llm_status": "missing",
            "symbolic_passed": False,
            "packet": {"skip_reasons": ["NO_KEY"]},
        },
        system={"lane_registry": {"status": "PASS"}},
        registry={},
    )

    seen: dict[str, object] = {}

    def fake_generate(system: str, user: str, **kwargs: object) -> GenerateResult:
        seen["system"] = system
        seen["user"] = user
        seen["kwargs"] = kwargs
        return GenerateResult("Console answer.", model="test")

    with patch("workbench.ui.analyst_panel.llm_client.generate", side_effect=fake_generate):
        text = analyst_panel._workbench_console_reply(snapshot, "What changed?")

    assert text == "Console answer."
    assert seen["kwargs"] == {"model": analyst_panel.llm_client.DEFAULT_RESEARCH_MODEL, "num_predict": 1024}
    system_prompt = str(seen["system"])
    user_prompt = str(seen["user"])
    assert "promotion, deployment, order, or live-routing authority" in system_prompt
    assert "packet_status" in user_prompt
    assert "bitcoin_edge_packets" in user_prompt
    assert "SCHEMA_ONLY_INACTIVE" in user_prompt
    assert "QUEUE_ONLY" in user_prompt
    assert "PREFILTER_ONLY" in user_prompt
    assert "clamped_or_ignored_until_tested_params_exist" in user_prompt


def test_workbench_console_reply_reports_llm_error() -> None:
    from workbench.ui.analyst_panel import _workbench_console_reply

    snapshot = SimpleNamespace(source="all_lanes", run_id="", state="idle", current_stage="")
    with patch(
        "workbench.ui.analyst_panel.llm_client.generate",
        return_value=GenerateResult(None, error="missing key", model="test"),
    ):
        text = _workbench_console_reply(snapshot, "Can you inspect the packet?")

    assert text == "LLM error: missing key"
