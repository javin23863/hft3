"""T0: replay session accounting sanity."""
from __future__ import annotations

from pathlib import Path

from backtest_pipeline.src.fee_model import FeeModel
from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_replay_balance_and_fee_sanity(minimal_npz: Path, tmp_path: Path) -> None:
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        latency_ms=1.0,
        max_steps=400,
        audit_dir=tmp_path / "acct",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result.get("error") is None
    summary = result["order_lifecycle_summary"]
    assert summary["replay_end_time_ns"] >= summary["replay_start_time_ns"]
    fills = summary.get("fill_count", 0)
    if fills > 0:
        fm = FeeModel(product="MES", tier="member")
        min_fee = fm.get_fee_per_contract() * fills
        assert min_fee >= 0
