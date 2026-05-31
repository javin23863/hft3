"""Replay must emit OrderIntent through execution adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE_NPZ = _REPO / "tests" / "fixtures" / "replay_minimal_mbo.npz"


@pytest.fixture(scope="module")
def minimal_npz() -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    if not _FIXTURE_NPZ.is_file():
        build_minimal_mbo_npz(_FIXTURE_NPZ)
    return _FIXTURE_NPZ


def test_replay_emits_order_intents(minimal_npz: Path, tmp_path: Path) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"

    from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
    from replay.replay_session import ReplaySession, ReplaySessionConfig

    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        latency_ms=1.0,
        max_steps=500,
        audit_dir=tmp_path / "audits",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()

    assert result.get("error") is None, result
    assert result["order_intent_count"] > 0
    summary = result["order_lifecycle_summary"]
    assert summary["accepted_count"] > 0
    assert summary["live_broker_call_count"] == 0
    assert summary["rithmic_order_call_count"] == 0

    lc_path = Path(result["lifecycle_path"])
    assert lc_path.is_file()
    lines = [json.loads(x) for x in lc_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(row["event_type"] == "ORDER_ACCEPTED" for row in lines)
