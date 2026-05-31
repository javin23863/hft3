"""T2: minimal fixture with known signal direction."""
from __future__ import annotations

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_toy_always_long_emits_buy_intents(minimal_npz, tmp_path) -> None:
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        max_steps=300,
        audit_dir=tmp_path / "alpha",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result["order_intent_count"] > 0
    summary = result["order_lifecycle_summary"]
    assert summary["accepted_count"] > 0
