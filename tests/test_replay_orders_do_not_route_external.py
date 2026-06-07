"""REPLAY mode must not route external broker orders."""
from __future__ import annotations

import os

import pytest

from execution.adapter_factory import create_adapter, forbid_broker_adapter_in_replay
from execution.adapters.broker import BrokerAdapter
from execution import safety


def test_replay_factory_rejects_broker_adapter() -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    with pytest.raises(ValueError, match="requires hbt"):
        create_adapter("REPLAY")

    broker = BrokerAdapter(run_id="x")
    with pytest.raises(RuntimeError):
        forbid_broker_adapter_in_replay(broker)


def test_replay_counters_zero_after_session(tmp_path) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    safety.reset_counters()

    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
    from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
    from replay.replay_session import ReplaySession, ReplaySessionConfig

    npz = tmp_path / "t.npz"
    build_minimal_mbo_npz(npz)
    cfg = ReplaySessionConfig(npz_path=str(npz), max_steps=200, audit_dir=tmp_path / "a")
    ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert safety.broker_call_count == 0
    assert safety.rithmic_order_call_count == 0
