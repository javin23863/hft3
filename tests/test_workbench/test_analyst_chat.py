"""Tests for analyst chat helper."""

from __future__ import annotations

import json
from pathlib import Path
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
