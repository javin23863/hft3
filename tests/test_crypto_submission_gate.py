"""K1 submission-gate tests — CRYPTO_LIVE.md §8 row K1."""
from __future__ import annotations

from pathlib import Path

import pytest

from execution import safety
from execution.adapters.crypto_broker import (
    CryptoLiveBrokerAdapter,
    CryptoPaperBrokerAdapter,
    CryptoRateLimitError,
    CryptoTransportError,
)
from execution.interfaces import OrderEventType, OrderIntent, new_intent_id


# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, *, raise_rate_limit: bool = False, raise_cancel: bool = False) -> None:
        self.order_new_calls: list[dict] = []
        self.order_cancel_calls: list = []
        self.cancel_all_calls: int = 0
        self._raise_rate_limit = raise_rate_limit
        self._raise_cancel = raise_cancel
        self._open_orders: list = []

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        if self._raise_rate_limit:
            raise CryptoRateLimitError("fake rate limit")
        return [0, "SUCCESS", None, None, [[None, None, body.get("cid"), None, None]]]

    def order_cancel(self, body: dict):
        self.order_cancel_calls.append(body)
        if self._raise_cancel:
            raise CryptoTransportError("fake cancel failure")
        return [0, "SUCCESS"]

    def cancel_all(self):
        self.cancel_all_calls += 1
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
        timestamp_ns=1_000_000,
        strategy_id="s",
        model_id="m",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        price=30_000.0,
        quantity=0.01,
    )


# ---------------------------------------------------------------------------
# K1 tests
# ---------------------------------------------------------------------------

def test_counter_unchanged_after_risk_block(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(
        run_id="t", transport=transport, risk_check=lambda i: False
    )
    safety.reset_counters()
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert ev.rejection_reason == "risk_blocked"
    assert safety.crypto_order_call_count == 0
    assert len(transport.order_new_calls) == 0


def test_counter_advances_on_allowed_submit(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(
        run_id="t", transport=transport, risk_check=lambda i: True
    )
    safety.reset_counters()
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_ACCEPTED
    assert safety.crypto_order_call_count == 1
    assert len(transport.order_new_calls) == 1


def test_single_call_site_grep():
    execution_root = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "execution"
    )
    canonical = execution_root / "adapters" / "crypto_broker.py"
    all_hits: list[tuple[Path, str]] = []
    for py in execution_root.rglob("*.py"):
        lines = py.read_text(encoding="utf-8").splitlines()
        for ln in lines:
            if ".order_new(" in ln and not ln.lstrip().startswith("#"):
                all_hits.append((py, ln))
    # Exactly one hit, and it must live in adapters/crypto_broker.py
    assert len(all_hits) == 1, (
        f"Expected exactly 1 .order_new( call site across packages/execution/, "
        f"found {len(all_hits)}: {all_hits}"
    )
    assert all_hits[0][0] == canonical, (
        f".order_new( must be only in {canonical}, found in {all_hits[0][0]}"
    )


def test_duplicate_cid_rejected(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    safety.reset_counters()
    iid = new_intent_id()
    ev1 = adapter.submit_order(_intent(iid))
    ev2 = adapter.submit_order(_intent(iid))
    assert ev1.event_type == OrderEventType.ORDER_ACCEPTED
    assert ev2.event_type == OrderEventType.ORDER_REJECTED
    assert ev2.rejection_reason == "duplicate_cid"
    assert safety.crypto_order_call_count == 1
    assert len(transport.order_new_calls) == 1


def test_rate_limit_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport(raise_rate_limit=True)
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    safety.reset_counters()
    intent = _intent()
    ev = adapter.submit_order(intent)
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 1
    # counter records the venue ATTEMPT even on transport failure
    assert safety.crypto_order_call_count == 1
    # cid was NOT poisoned: after transport stops raising, same intent retries successfully
    transport._raise_rate_limit = False
    ev2 = adapter.submit_order(intent)
    assert ev2.event_type == OrderEventType.ORDER_ACCEPTED
    assert len(transport.order_new_calls) == 2


def test_live_default_risk_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    transport = FakeTransport()
    adapter = CryptoLiveBrokerAdapter(run_id="t", transport=transport, risk_check=None)
    safety.reset_counters()
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert ev.rejection_reason == "no_risk_layer_wired"
    assert safety.crypto_order_call_count == 0
    assert len(transport.order_new_calls) == 0


def test_kill_path_cancel_all_on_close(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    adapter.close()
    assert transport.cancel_all_calls == 1


def test_reconcile_reports_diff(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    unknown_cid = 999999
    transport._open_orders = [[None, None, unknown_cid, None, None]]
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    result = adapter.reconcile()
    assert "unknown_at_venue" in result
    assert "missing_locally" in result
    assert "venue_open" in result
    assert "local_pending" in result
    assert unknown_cid in result["missing_locally"]


def test_risk_check_exception_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()

    def exploding_risk(intent):
        raise ValueError("simulated risk crash")

    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport, risk_check=exploding_risk)
    safety.reset_counters()
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert ev.rejection_reason.startswith("risk_check_error")
    assert safety.crypto_order_call_count == 0
    assert len(transport.order_new_calls) == 0


def test_cancel_failure_not_silent(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport(raise_cancel=True)
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    ev = adapter.cancel_order("CRYPTO-12345")
    assert ev.event_type == OrderEventType.ORDER_CANCEL_REQUESTED
    assert ev.rejection_reason.startswith("cancel_failed")
    # must never produce ORDER_CANCELLED when transport raises
    all_types = [e.event_type for e in adapter._events]
    assert OrderEventType.ORDER_CANCELLED not in all_types


def test_cancel_sends_cid_and_date(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)
    ev_submit = adapter.submit_order(_intent())
    assert ev_submit.event_type == OrderEventType.ORDER_ACCEPTED
    order_id = ev_submit.order_id
    ev_cancel = adapter.cancel_order(order_id)
    assert ev_cancel.event_type == OrderEventType.ORDER_CANCELLED
    assert len(transport.order_cancel_calls) == 1
    cancel_body = transport.order_cancel_calls[0]
    assert "cid" in cancel_body
    assert isinstance(cancel_body["cid"], int)
    assert "cid_date" in cancel_body
    # cid_date must be a YYYY-MM-DD string
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cancel_body["cid_date"])
