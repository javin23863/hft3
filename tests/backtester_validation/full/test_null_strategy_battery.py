"""T2: zero-alpha / flat strategies expect ~0 edge."""
from __future__ import annotations

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


class _NeverTradeStrategy(ToyAlwaysLongStrategy):
    def on_step(self, ctx):  # type: ignore[override]
        return []


def test_flat_strategy_zero_intents(minimal_npz, tmp_path) -> None:
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        max_steps=100,
        audit_dir=tmp_path / "flat",
    )
    result = ReplaySession(cfg, _NeverTradeStrategy()).run()
    assert result["order_intent_count"] == 0
