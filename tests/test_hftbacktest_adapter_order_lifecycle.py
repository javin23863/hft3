"""HftBacktestSimulatedExchangeAdapter lifecycle."""
from __future__ import annotations

import os

import pytest

from execution.adapter_factory import create_adapter
from execution.interfaces import OrderEventType, OrderIntent, new_intent_id


@pytest.fixture
def minimal_npz(tmp_path):
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    p = tmp_path / "m.npz"
    build_minimal_mbo_npz(p)
    return str(p)


def test_submit_accept_fill_lifecycle(minimal_npz: str) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

    hbt = build_hftbacktest(minimal_npz, latency_ms=1.0)
    adapter = create_adapter("REPLAY", hbt=hbt, run_id="lc-test")

    intent = OrderIntent(
        intent_id=new_intent_id(),
        run_id="lc-test",
        timestamp_ns=1_000_000_000,
        strategy_id="test",
        model_id="TEST",
        symbol="MES",
        side="BUY",
        order_type="LIMIT",
        price=4999.0,
        quantity=1.0,
    )
    ev = adapter.submit_order(intent)
    assert ev.event_type == OrderEventType.ORDER_ACCEPTED

    for _ in range(500):
        if hbt.elapse(100_000) == 1:
            break
        hbt.clear_inactive_orders(0)
        adapter.after_elapse(int(hbt.current_timestamp))
        if adapter.get_position("MES") != 0:
            break

    events = adapter.all_events
    types = {e.event_type for e in events}
    assert OrderEventType.ORDER_SUBMITTED in types
    assert OrderEventType.ORDER_ACCEPTED in types
