"""Latency bands reflected in replay lifecycle summary."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def minimal_npz(tmp_path: Path) -> str:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    p = tmp_path / "lat.npz"
    build_minimal_mbo_npz(p)
    return str(p)


@pytest.mark.parametrize("latency_ms", [0.5, 1.0, 2.0])
def test_latency_band_in_summary(minimal_npz: str, latency_ms: float, tmp_path: Path) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
    from replay.replay_session import ReplaySession, ReplaySessionConfig

    cfg = ReplaySessionConfig(
        npz_path=minimal_npz,
        latency_ms=latency_ms,
        max_steps=250,
        audit_dir=tmp_path / f"band_{latency_ms}",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result["order_lifecycle_summary"]["latency_band_ms"] == latency_ms
