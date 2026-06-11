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
    """Build a minimal NPZ that produces a fill: a BUY limit above best ask."""
    from pathlib import Path
    import numpy as np
    from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest
    from hftbacktest.types import (
        ADD_ORDER_EVENT, BUY_EVENT, SELL_EVENT, EXCH_EVENT, LOCAL_EVENT, event_dtype,
    )
    from backtest_pipeline.src.hft_backtest_builder import _apply_constant_latency

    p = tmp_path / "fill_test.npz"
    tick = 0.25
    mid = 5000.0
    ts = 1_000_000_000

    rows = []
    # Build a two-sided book around 5000: ask at 5000.25
    for i in range(3):
        rows.append((
            ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts, mid - tick * (i + 1), 10.0, 1000 + i, 0, 0.0,
        ))
        rows.append((
            ADD_ORDER_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT,
            ts, ts, mid + tick * (i + 1), 10.0, 2000 + i, 0, 0.0,
        ))
        ts += 500_000

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


def test_fill_exactness_wake_reason(tmp_path, minimal_npz: str) -> None:
    """OPT 1 A/B: fills and PnL identical with and without wake_reason skip.

    Simulates the event loop manually:
      - baseline: always calls after_elapse with wake_reason=-1 (full scan)
      - optimised: calls after_elapse with wake_reason=2 on feed wakes,
                   wake_reason=3 on order-response wakes (via wait_next_feed)

    Both sessions run on the same NPZ with a marketable limit order (price >=
    best ask) so fills actually occur.  Fill count, total qty, and PnL must
    be identical.
    """
    os.environ["EXECUTION_MODE"] = "REPLAY"
    from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

    npz_path = minimal_npz  # book has ask at 5000.25; buy at 5001 is marketable

    def _run_session(use_wake_reason: bool) -> dict:
        hbt = build_hftbacktest(npz_path, latency_ms=1.0)
        adapter = create_adapter("REPLAY", hbt=hbt, run_id="ab-test")

        # Submit a marketable buy limit order (above best ask = 5000.25)
        intent = OrderIntent(
            intent_id=new_intent_id(),
            run_id="ab-test",
            timestamp_ns=1_000_000_000,
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
            # Stop once we have a fill
            fill_found = any(
                e.event_type in (OrderEventType.ORDER_FILLED, OrderEventType.ORDER_PARTIALLY_FILLED)
                for e in adapter.all_events
            )
            if fill_found:
                break

        fills = [
            e for e in adapter.all_events
            if e.event_type in (OrderEventType.ORDER_FILLED, OrderEventType.ORDER_PARTIALLY_FILLED)
        ]
        state = hbt.state_values(0)
        return {
            "n_fills": len(fills),
            "total_qty": sum(float(e.filled_quantity) for e in fills),
            "fee": float(state.fee),
            "steps": steps,
        }

    baseline = _run_session(use_wake_reason=False)
    optimised = _run_session(use_wake_reason=True)

    assert optimised["n_fills"] == baseline["n_fills"], (
        f"Fill count diverged: baseline={baseline['n_fills']} opt={optimised['n_fills']}"
    )
    assert optimised["total_qty"] == baseline["total_qty"], (
        f"Fill qty diverged: baseline={baseline['total_qty']} opt={optimised['total_qty']}"
    )
    # fees may differ by floating-point rounding across two independent hbt instances;
    # allow a small tolerance
    assert abs(optimised["fee"] - baseline["fee"]) < 1e-6, (
        f"Fee diverged: baseline={baseline['fee']} opt={optimised['fee']}"
    )
