"""T2: full replay parity bundle."""
from __future__ import annotations

import json
from pathlib import Path

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from execution import safety
from execution.adapter_factory import create_adapter, forbid_live_adapter_in_replay
from execution.adapters.live_broker import LiveBrokerAdapter
from replay.replay_clock import ReplayClock, deterministic_run_id
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_replay_clock_and_session_bundle(minimal_npz: Path, tmp_path: Path) -> None:
    clock = ReplayClock()
    clock.advance_to(100)
    clock.advance_to(200)
    assert clock.now_ns == 200
    assert deterministic_run_id("x", 1.0, "LogProbQueueModel2", 0) == deterministic_run_id(
        "x", 1.0, "LogProbQueueModel2", 0
    )

    safety.reset_counters()
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        latency_ms=1.0,
        max_steps=400,
        audit_dir=tmp_path / "full",
    )
    result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert result.get("error") is None
    assert result["order_intent_count"] > 0
    assert result["certification_stamp"]["promotion_label"] in (
        "UNCERTIFIED",
        "STALE_CERTIFICATION",
        "RESEARCH_ONLY",
        "NOT_TRUSTED",
        "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE",
    )
    lc_path = Path(result["lifecycle_path"])
    lines = [json.loads(x) for x in lc_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(row["event_type"] == "ORDER_ACCEPTED" for row in lines)
    assert safety.live_broker_call_count == 0

    live = LiveBrokerAdapter(run_id="x")
    try:
        forbid_live_adapter_in_replay(live)
        raised = False
    except RuntimeError:
        raised = True
    assert raised

    try:
        create_adapter("REPLAY")
        raised_hbt = False
    except ValueError:
        raised_hbt = True
    assert raised_hbt
