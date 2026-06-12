"""C8 latency capture tests — LATENCY.md §10 honest order-ack timing."""
from __future__ import annotations

import time

import pytest

from execution import safety
from execution.adapters.crypto_broker import (
    CryptoPaperBrokerAdapter,
    CryptoTransportError,
    LatencySample,
)
from execution.interfaces import OrderEventType, OrderIntent, new_intent_id


# ---------------------------------------------------------------------------
# Fake monotonic clock
# ---------------------------------------------------------------------------

class _FakeClock:
    """Fake perf_counter_ns: each call advances by `step_ns`."""

    def __init__(self, start: int = 1_000_000_000, step_ns: int = 500_000) -> None:
        self._val = start
        self._step = step_ns

    def __call__(self) -> int:
        v = self._val
        self._val += self._step
        return v


# ---------------------------------------------------------------------------
# FakeTransport with controllable failure
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, *, raise_transport: bool = False) -> None:
        self.order_new_calls: list[dict] = []
        self._raise_transport = raise_transport
        self._open_orders: list = []

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        if self._raise_transport:
            raise CryptoTransportError("fake transport error")
        return [0, "SUCCESS", None, None, [[None, None, body.get("cid"), None, None]]]

    def order_cancel(self, body: dict):
        return [0, "SUCCESS"]

    def cancel_all(self):
        return [0, "SUCCESS"]

    def open_orders(self):
        return self._open_orders


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _intent(intent_id: str | None = None) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id or new_intent_id(),
        run_id="test",
        timestamp_ns=time.time_ns(),
        strategy_id="s",
        model_id="m",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        price=30_000.0,
        quantity=0.01,
    )


def _adapter(transport, *, risk_check=None):
    if risk_check is None:
        risk_check = lambda i: True
    return CryptoPaperBrokerAdapter(
        run_id="t", transport=transport, risk_check=risk_check
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_accepted_sample_recorded(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    clock = _FakeClock(start=1_000_000_000, step_ns=500_000)
    import execution.adapters.crypto_broker as cb_mod
    monkeypatch.setattr(cb_mod.time, "perf_counter_ns", clock)

    transport = FakeTransport()
    adapter = _adapter(transport)
    safety.reset_counters()

    intent = _intent()
    ev = adapter.submit_order(intent)

    assert ev.event_type == OrderEventType.ORDER_ACCEPTED
    samples = adapter.drain_latency_samples()
    assert len(samples) == 1
    s = samples[0]
    assert s.accepted is True
    # submit_ns is first perf_counter_ns call (clock returns 1_000_000_000),
    # ack_ns is second call (1_000_000_000 + 500_000 = 1_000_500_000)
    assert s.ack_ns - s.submit_ns == 500_000
    assert s.latency_ms == pytest.approx(0.5)
    assert s.measurement_tier == "rest_sync"
    assert s.shadow_synthetic is False
    assert s.intent_id == intent.intent_id


def test_event_carries_latency_ms(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    clock = _FakeClock(start=2_000_000_000, step_ns=1_000_000)
    import execution.adapters.crypto_broker as cb_mod
    monkeypatch.setattr(cb_mod.time, "perf_counter_ns", clock)

    transport = FakeTransport()
    adapter = _adapter(transport)
    safety.reset_counters()

    ev = adapter.submit_order(_intent())

    assert ev.event_type == OrderEventType.ORDER_ACCEPTED
    samples = adapter.drain_latency_samples()
    assert len(samples) == 1
    assert ev.latency_ms == pytest.approx(samples[0].latency_ms)
    assert ev.latency_ms == pytest.approx(1.0)


def test_failed_send_recorded_as_unaccepted(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    clock = _FakeClock(start=3_000_000_000, step_ns=750_000)
    import execution.adapters.crypto_broker as cb_mod
    monkeypatch.setattr(cb_mod.time, "perf_counter_ns", clock)

    transport = FakeTransport(raise_transport=True)
    adapter = _adapter(transport)
    safety.reset_counters()

    ev = adapter.submit_order(_intent())

    assert ev.event_type == OrderEventType.ORDER_REJECTED
    samples = adapter.drain_latency_samples()
    assert len(samples) == 1
    s = samples[0]
    assert s.accepted is False
    assert s.ack_ns > s.submit_ns
    assert s.latency_ms > 0.0


def test_risk_block_no_sample(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = _adapter(transport, risk_check=lambda i: False)
    safety.reset_counters()

    ev = adapter.submit_order(_intent())

    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 0
    samples = adapter.drain_latency_samples()
    assert samples == []


def test_duplicate_cid_no_sample(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = _adapter(transport)
    safety.reset_counters()

    iid = new_intent_id()
    ev1 = adapter.submit_order(_intent(iid))
    assert ev1.event_type == OrderEventType.ORDER_ACCEPTED
    adapter.drain_latency_samples()  # clear first sample

    ev2 = adapter.submit_order(_intent(iid))
    assert ev2.event_type == OrderEventType.ORDER_REJECTED
    assert ev2.rejection_reason == "duplicate_cid"
    samples = adapter.drain_latency_samples()
    assert samples == []


def test_drain_clears(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = _adapter(transport)
    safety.reset_counters()

    adapter.submit_order(_intent())
    adapter.submit_order(_intent())

    first_drain = adapter.drain_latency_samples()
    assert len(first_drain) == 2

    second_drain = adapter.drain_latency_samples()
    assert second_drain == []
