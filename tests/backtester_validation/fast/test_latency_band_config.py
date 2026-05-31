"""T0: latency band reflected in replay lifecycle summary."""
from __future__ import annotations

from pathlib import Path

import pytest

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


@pytest.mark.parametrize("latency_ms", [0.5, 1.0, 2.0])
def test_latency_band_in_summary(minimal_npz: Path, latency_ms: float, tmp_path: Path) -> None:
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        latency_ms=latency_ms,
        max_steps=250,
        audit_dir=tmp_path / f"band_{latency_ms}",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result["order_lifecycle_summary"]["latency_band_ms"] == latency_ms
