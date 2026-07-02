from __future__ import annotations

from types import SimpleNamespace

from hbt_stub import _ensure_hftbacktest_stub

_ensure_hftbacktest_stub()

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from execution.interfaces import OrderEvent, OrderEventType
from replay.replay_session import ReplayStepContext


class _StubHypothesis:
    hyp_id = 1

    def __init__(self, signal: float = 0.0) -> None:
        self.signal = signal

    def evaluate(self, _state) -> float:
        return self.signal


def _fill_event(price: float, side: str = "BUY") -> OrderEvent:
    return OrderEvent(
        order_id="SIM-1",
        intent_id="i-1",
        run_id="r",
        timestamp_ns=1_000,
        receive_timestamp_ns=1_000,
        event_type=OrderEventType.ORDER_FILLED,
        symbol="MES",
        side=side,
        price=price,
        quantity=1.0,
        filled_quantity=1.0,
        avg_fill_price=price,
    )


def _ctx(
    *,
    now_ns: int,
    best_bid: float,
    best_ask: float,
    position: float,
    order_events: list | None = None,
) -> ReplayStepContext:
    return ReplayStepContext(
        run_id="r",
        clock=SimpleNamespace(now_ns=now_ns),
        market_state=object(),
        best_bid=best_bid,
        best_ask=best_ask,
        position=position,
        order_events=order_events or [],
        execution=None,
        symbol="MES",
    )


def test_stop_loss_exit_emits_marketable_sell() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(), stop_loss_pct=0.5)
    # Entry observed: long 1 @ 5000.
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0, order_events=[_fill_event(5000.0)]))
    # Mid drops > 0.5% below entry -> stop.
    intents = strategy.on_step(_ctx(now_ns=2_000, best_bid=4970.0, best_ask=4970.25, position=1.0))
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == "SELL"
    assert intent.reason_code == "stop_loss"
    assert intent.price == 4970.0
    assert intent.quantity == 1.0
    assert intent.reduce_only is True


def test_take_profit_exit_short_position() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(), take_profit_pct=0.5)
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=-1.0, order_events=[_fill_event(5000.0, side="SELL")]))
    # Mid drops > 0.5% below short entry -> take profit, BUY back at ask.
    intents = strategy.on_step(_ctx(now_ns=2_000, best_bid=4970.0, best_ask=4970.25, position=-1.0))
    assert len(intents) == 1
    assert intents[0].side == "BUY"
    assert intents[0].reason_code == "take_profit"
    assert intents[0].price == 4970.25


def test_max_holding_exit_from_holding_bars() -> None:
    strategy = HypothesisReplayStrategy(
        _StubHypothesis(), holding_period_bars=2, bar_duration_ns=1_000_000
    )
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0, order_events=[_fill_event(5000.0)]))
    # Before max holding: no exit.
    assert strategy.on_step(_ctx(now_ns=1_000_000, best_bid=4999.75, best_ask=5000.25, position=1.0)) == []
    # After 2 bars of 1ms: exit.
    intents = strategy.on_step(_ctx(now_ns=1_000 + 2_000_000, best_bid=4999.75, best_ask=5000.25, position=1.0))
    assert len(intents) == 1
    assert intents[0].reason_code == "max_holding"


def test_exit_reemit_guard_throttles_duplicates() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(), stop_loss_pct=0.5)
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0, order_events=[_fill_event(5000.0)]))
    first = strategy.on_step(_ctx(now_ns=2_000, best_bid=4970.0, best_ask=4970.25, position=1.0))
    assert len(first) == 1
    # Same stop condition immediately after: suppressed by re-emit guard.
    assert strategy.on_step(_ctx(now_ns=3_000, best_bid=4970.0, best_ask=4970.25, position=1.0)) == []
    # After the guard window with position still open: re-emitted.
    again = strategy.on_step(
        _ctx(now_ns=2_000 + strategy.EXIT_REEMIT_NS, best_bid=4970.0, best_ask=4970.25, position=1.0)
    )
    assert len(again) == 1


def test_no_exit_params_keeps_signal_only_behavior() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(signal=0.0))
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0, order_events=[_fill_event(5000.0)]))
    # Huge adverse move but no stop configured and signal flat: no intents.
    assert strategy.on_step(_ctx(now_ns=2_000, best_bid=4900.0, best_ask=4900.25, position=1.0)) == []


def test_entry_tracking_resets_when_flat() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(), stop_loss_pct=0.5)
    strategy.on_step(_ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0, order_events=[_fill_event(5000.0)]))
    # Position closed externally: tracking resets, no stale exit.
    assert strategy.on_step(_ctx(now_ns=2_000, best_bid=4970.0, best_ask=4970.25, position=0.0)) == []
    assert strategy._entry_price is None


def test_one_sided_book_still_allows_short_holding_exit() -> None:
    # Short position; bid side empty; ask available. Holding expiry must still
    # emit the BUY exit at the ask (the side needed to close exists).
    strategy = HypothesisReplayStrategy(
        _StubHypothesis(), holding_period_bars=1, bar_duration_ns=1_000_000
    )
    strategy.on_step(
        _ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=-1.0,
             order_events=[_fill_event(5000.0, side="SELL")])
    )
    ctx = _ctx(now_ns=2_001_000, best_bid=0.0, best_ask=5000.25, position=-1.0)
    ctx.book_one_sided = True
    intents = strategy.on_step(ctx)
    assert len(intents) == 1
    assert intents[0].side == "BUY"
    assert intents[0].price == 5000.25
    assert intents[0].reason_code == "max_holding"


def test_one_sided_book_defers_exit_when_needed_side_empty() -> None:
    # Long position needs the bid to close; bid side empty -> defer, retry later.
    strategy = HypothesisReplayStrategy(
        _StubHypothesis(), holding_period_bars=1, bar_duration_ns=1_000_000
    )
    strategy.on_step(
        _ctx(now_ns=1_000, best_bid=4999.75, best_ask=5000.25, position=1.0,
             order_events=[_fill_event(5000.0)])
    )
    ctx = _ctx(now_ns=2_001_000, best_bid=0.0, best_ask=5000.25, position=1.0)
    ctx.book_one_sided = True
    assert strategy.on_step(ctx) == []
    # Bid returns -> exit emitted (throttle does not consume deferred attempts).
    ctx2 = _ctx(now_ns=2_101_000, best_bid=4999.0, best_ask=5000.25, position=1.0)
    intents = strategy.on_step(ctx2)
    assert len(intents) == 1
    assert intents[0].side == "SELL"
    assert intents[0].reason_code == "max_holding"


def test_one_sided_book_still_blocks_new_entries() -> None:
    strategy = HypothesisReplayStrategy(_StubHypothesis(signal=0.9))
    ctx = _ctx(now_ns=1_000, best_bid=0.0, best_ask=5000.25, position=0.0)
    ctx.book_one_sided = True
    assert strategy.on_step(ctx) == []
