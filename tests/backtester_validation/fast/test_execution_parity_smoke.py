"""T0: execution adapter smoke (replay + paper + live mock)."""
from __future__ import annotations

import os
from pathlib import Path

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from execution.adapter_factory import create_adapter
from execution.adapters.live_broker import LiveBrokerAdapter
from execution.adapters.paper_broker import PaperBrokerAdapter
from execution.interfaces import OrderIntent, new_intent_id
from replay.replay_session import ReplaySession, ReplaySessionConfig


def test_toy_strategy_replay_smoke(minimal_npz: Path, tmp_path: Path) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    cfg = ReplaySessionConfig(
        npz_path=str(minimal_npz),
        max_steps=300,
        audit_dir=tmp_path / "replay",
    )
    replay_result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert replay_result["order_intent_count"] > 0

    os.environ["EXECUTION_MODE"] = "PAPER"
    paper = create_adapter("PAPER", run_id="test-paper")
    assert isinstance(paper, PaperBrokerAdapter)
    intent = OrderIntent(
        intent_id=new_intent_id(),
        run_id="test",
        timestamp_ns=1,
        strategy_id="toy",
        model_id="TOY",
        symbol="MES",
        side="BUY",
        order_type="LIMIT",
        price=5000.0,
        quantity=1.0,
    )
    ev = paper.submit_order(intent)
    assert ev.event_type.value == "ORDER_ACCEPTED"

    os.environ["LIVE_MAX_ORDER_SIZE"] = "1"
    os.environ["LIVE_DAILY_LOSS_LIMIT"] = "1000"
    os.environ["LIVE_KILL_SWITCH"] = "armed"
    os.environ["LIVE_RISK_ENABLED"] = "1"
    os.environ["EXECUTION_MODE"] = "LIVE"
    live = create_adapter("LIVE", run_id="test-live")
    assert isinstance(live, LiveBrokerAdapter)
    live.submit_order(intent)

    os.environ["EXECUTION_MODE"] = "REPLAY"

    source = Path(__file__).resolve().parents[3] / "packages" / "backtest_pipeline" / "src" / "hypothesis_replay_strategy.py"
    if not source.is_file():
        source = Path(__file__).resolve().parents[3] / "backtest_pipeline" / "src" / "hypothesis_replay_strategy.py"
    text = source.read_text(encoding="utf-8").lower()
    assert 'if mode == "replay"' not in text
    assert 'if mode == "paper"' not in text
    assert 'if mode == "live"' not in text
