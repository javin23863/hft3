"""T2: queue model monotonicity vs latency."""
from __future__ import annotations

import pytest

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


@pytest.mark.parametrize("latency_ms", [0.5, 1.0, 5.0])
def test_higher_latency_band_recorded(minimal_npz, latency_ms: float, tmp_path) -> None:
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        latency_ms=latency_ms,
        max_steps=200,
        audit_dir=tmp_path / f"q_{latency_ms}",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result["order_lifecycle_summary"]["latency_band_ms"] == latency_ms
