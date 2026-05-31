"""T2: timestamp shuffle / book corruption must fail loud."""
from __future__ import annotations

import pytest

from replay.replay_clock import ReplayClock


def test_replay_clock_rejects_backward_time() -> None:
    clock = ReplayClock()
    clock.advance_to(500)
    clock.advance_to(100)
    assert clock.now_ns == 500


def test_replay_session_missing_npz_fails_loud(tmp_path) -> None:
    from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
    from replay.replay_session import ReplaySession, ReplaySessionConfig

    cfg = ReplaySessionConfig(
        npz_path=str(tmp_path / "missing.npz"),
        max_steps=10,
        audit_dir=tmp_path / "bad",
    )
    with pytest.raises(FileNotFoundError):
        ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
