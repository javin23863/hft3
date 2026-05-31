"""T0: REPLAY must not route live/Rithmic orders."""
from __future__ import annotations

import os

import pytest

from execution import safety
from execution.adapter_factory import create_adapter, forbid_live_adapter_in_replay
from execution.adapters.live_broker import LiveBrokerAdapter
from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_replay_factory_rejects_live_adapter() -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    with pytest.raises(ValueError, match="requires hbt"):
        create_adapter("REPLAY")

    live = LiveBrokerAdapter(run_id="x")
    with pytest.raises(RuntimeError):
        forbid_live_adapter_in_replay(live)


def test_replay_counters_zero_after_session(minimal_npz_tmp, tmp_path) -> None:
    safety.reset_counters()
    cfg = ReplaySessionConfig(npz_path=str(minimal_npz_tmp), max_steps=200, audit_dir=tmp_path / "a")
    ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert safety.live_broker_call_count == 0
    assert safety.rithmic_order_call_count == 0
