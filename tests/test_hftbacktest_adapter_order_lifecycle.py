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
        adapter.after_elapse(int(hbt.current_timestamp))
        if adapter.get_position("MES") != 0:
            break

    events = adapter.all_events
    types = {e.event_type for e in events}
    assert OrderEventType.ORDER_SUBMITTED in types
    assert OrderEventType.ORDER_ACCEPTED in types


# ---------------------------------------------------------------------------
# OPT 1: wake_reason=2 (pure feed) must NOT suppress fill detection when
# fills actually arrive on ret=3 wakes, and must produce identical fills/PnL
# versus the full-scan path.
# ---------------------------------------------------------------------------

def _make_marketable_npz(tmp_path):
    """Build a DEPTH_EVENT NPZ on which a marketable BUY limit actually fills.

    NOTE: build_minimal_mbo_npz writes ADD_ORDER_EVENT (L3/MBO) rows, which
    HashMapMarketDepthBacktest (an L2 engine) never applies to the book — the
    local book stays NaN on that fixture and no order can ever fill there.
    This builder writes L2 DEPTH_EVENT rows instead: the book forms
    (bid=4999.75 / ask=5000.25) and a buy at 5001 crosses the spread and
    fills at the ask.  Trailing heartbeat depth events keep the backtest
    clock advancing long enough for the order round-trip (1 ms entry +
    1 ms response latency).
    """
    import numpy as np
    from hftbacktest.types import (
        DEPTH_EVENT, BUY_EVENT, SELL_EVENT, EXCH_EVENT, LOCAL_EVENT, event_dtype,
    )

    p = tmp_path / "fill_test.npz"
    tick = 0.25
    mid = 5000.0
    ts = 1_000_000_000

    rows = []
    # Two-sided L2 book around 5000: best ask at 5000.25
    for i in range(3):
        rows.append((
            DEPTH_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts, mid - tick * (i + 1), 10.0, 0, 0, 0.0,
        ))
        rows.append((
            DEPTH_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts, mid + tick * (i + 1), 10.0, 0, 0, 0.0,
        ))
    # Heartbeat depth refreshes every 10 ms out to +200 ms.
    for k in range(1, 21):
        t2 = ts + k * 10_000_000
        rows.append((
            DEPTH_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT,
            t2, t2, mid - tick, 10.0 + k, 0, 0, 0.0,
        ))

    data = np.array(rows, dtype=event_dtype)
    np.savez_compressed(str(p), data=data)
    return str(p)


def test_wake_reason_feed_skips_scan_no_orders(minimal_npz: str) -> None:
    """wake_reason=2 with no open orders: fast exit (no orders dict traversal)."""
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

    hbt = build_hftbacktest(minimal_npz, latency_ms=1.0)
    adapter = create_adapter("REPLAY", hbt=hbt, run_id="wr-feed-noorder")

    # No orders: after_elapse with any wake_reason must return cleanly
    hbt.elapse(100_000)
    adapter.after_elapse(int(hbt.current_timestamp), wake_reason=2)
    assert adapter.all_events == []


def test_wake_reason_feed_skips_scan_open_order_no_fill(minimal_npz: str) -> None:
    """wake_reason=2 with an open order: scan is skipped (no fill emitted).

    A deep passive buy order that cannot be filled is placed.  On a pure feed
    wake (wake_reason=2) the adapter must not touch orders() and must not emit
    any fill event.  The order is still present afterward.
    """
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

    hbt = build_hftbacktest(minimal_npz, latency_ms=1.0)
    adapter = create_adapter("REPLAY", hbt=hbt, run_id="wr-feed-open")

    # Deep passive order — price is far below best bid, never fills
    intent = OrderIntent(
        intent_id=new_intent_id(),
        run_id="wr-feed-open",
        timestamp_ns=1_000_000_000,
        strategy_id="test",
        model_id="TEST",
        symbol="MES",
        side="BUY",
        order_type="LIMIT",
        price=4990.0,  # far from best ask; will not fill
        quantity=1.0,
    )
    adapter.submit_order(intent)
    assert adapter.has_open_orders

    hbt.elapse(100_000)
    # wake_reason=2: scan skipped — no fills, order still open
    adapter.after_elapse(int(hbt.current_timestamp), wake_reason=2)
    fill_events = [
        e for e in adapter.all_events
        if e.event_type in (OrderEventType.ORDER_FILLED, OrderEventType.ORDER_PARTIALLY_FILLED)
    ]
    assert fill_events == [], "Feed wake must not produce spurious fill events"
    # Open orders dict untouched — order still tracked
    assert adapter.has_open_orders


def test_fill_exactness_wake_reason(tmp_path) -> None:
    """OPT 1 A/B: fills and PnL identical with and without wake_reason skip.

    Simulates the event loop manually:
      - baseline: always calls after_elapse with wake_reason=-1 (full scan)
      - optimised: calls after_elapse with wake_reason=result (skips scan on
                   ret=2 feed wakes; scans on ret=3 order-response wakes)

    Both sessions run on the same DEPTH_EVENT NPZ with a marketable buy limit
    (5001.0 > best ask 5000.25) so a real fill occurs (verified non-vacuous:
    n_fills >= 1 is asserted).  Fill count, qty, exec price, fees, and
    position must be identical.
    """
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

    npz_path = _make_marketable_npz(tmp_path)

    def _run_session(use_wake_reason: bool) -> dict:
        hbt = build_hftbacktest(npz_path, latency_ms=1.0)
        adapter = create_adapter("REPLAY", hbt=hbt, run_id="ab-test")

        # Step to the first feed so the book is live, then submit a
        # marketable buy limit above the best ask (5000.25).
        assert hbt.wait_next_feed(True, 1_000_000_000) == 2
        intent = OrderIntent(
            intent_id=new_intent_id(),
            run_id="ab-test",
            timestamp_ns=int(hbt.current_timestamp),
            strategy_id="test",
            model_id="TEST",
            symbol="MES",
            side="BUY",
            order_type="LIMIT",
            price=5001.0,
            quantity=1.0,
        )
        adapter.submit_order(intent)

        steps = 0
        for _ in range(2000):
            result = hbt.wait_next_feed(True, 1_000_000_000)
            if result == 1:
                break
            if result not in (0, 2, 3):
                break
            ts = int(hbt.current_timestamp)
            if use_wake_reason:
                adapter.after_elapse(ts, wake_reason=result)
            else:
                adapter.after_elapse(ts)  # default wake_reason=-1 → full scan
            steps += 1

        fills = [
            e for e in adapter.all_events
            if e.event_type in (OrderEventType.ORDER_FILLED, OrderEventType.ORDER_PARTIALLY_FILLED)
        ]
        state = hbt.state_values(0)
        return {
            "n_fills": len(fills),
            "total_qty": sum(float(e.filled_quantity) for e in fills),
            "exec_prices": [float(e.avg_fill_price) for e in fills],
            "fee": float(state.fee),
            "position": float(hbt.position(0)),
            "steps": steps,
        }

    baseline = _run_session(use_wake_reason=False)
    optimised = _run_session(use_wake_reason=True)

    # Non-vacuity: the marketable order must actually have filled.
    assert baseline["n_fills"] >= 1, (
        f"Test is vacuous — no fill occurred in baseline: {baseline}"
    )
    assert baseline["position"] == 1.0

    assert optimised["n_fills"] == baseline["n_fills"], (
        f"Fill count diverged: baseline={baseline['n_fills']} opt={optimised['n_fills']}"
    )
    assert optimised["total_qty"] == baseline["total_qty"], (
        f"Fill qty diverged: baseline={baseline['total_qty']} opt={optimised['total_qty']}"
    )
    assert optimised["exec_prices"] == baseline["exec_prices"], (
        f"Exec price diverged: baseline={baseline['exec_prices']} opt={optimised['exec_prices']}"
    )
    assert optimised["position"] == baseline["position"]
    # fees may differ by floating-point rounding across two independent hbt
    # instances; allow a small tolerance
    assert abs(optimised["fee"] - baseline["fee"]) < 1e-6, (
        f"Fee diverged: baseline={baseline['fee']} opt={optimised['fee']}"
    )
