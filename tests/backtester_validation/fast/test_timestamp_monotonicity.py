"""T0: replay clock monotonicity."""
from __future__ import annotations

from pathlib import Path

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_clock import ReplayClock, deterministic_run_id
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_replay_clock_monotonic() -> None:
    clock = ReplayClock()
    clock.advance_to(100)
    clock.advance_to(200)
    assert clock.now_ns == 200
    clock.advance_to(150)
    assert clock.now_ns == 200
    assert clock.tick(50) == 250


def test_deterministic_run_id_stable() -> None:
    a = deterministic_run_id("/data/x.npz", 1.0, "LogProbQueueModel2", 0)
    b = deterministic_run_id("/data/x.npz", 1.0, "LogProbQueueModel2", 0)
    assert a == b


def test_lifecycle_uses_replay_time(minimal_npz_tmp: Path, tmp_path: Path) -> None:
    cfg = ReplaySessionConfig(npz_path=str(minimal_npz_tmp), max_steps=200, audit_dir=tmp_path / "aud")
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    summary = result["order_lifecycle_summary"]
    assert summary["replay_end_time_ns"] >= summary["replay_start_time_ns"]
