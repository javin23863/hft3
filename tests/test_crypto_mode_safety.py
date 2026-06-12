"""K2 mode-safety tests — CRYPTO_LIVE.md §8 row K2."""
from __future__ import annotations

import pytest

from execution import safety
from execution.adapter_factory import create_adapter
from execution.adapters.crypto_broker import (
    CryptoLiveBrokerAdapter,
    CryptoPaperBrokerAdapter,
)


# ---------------------------------------------------------------------------
# FakeTransport (local copy — no cross-file import per flat convention)
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self) -> None:
        self.order_new_calls: list = []
        self.cancel_all_calls: int = 0

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        return [0, "SUCCESS"]

    def order_cancel(self, body: dict):
        return [0, "SUCCESS"]

    def cancel_all(self):
        self.cancel_all_calls += 1
        return [0, "SUCCESS"]

    def open_orders(self):
        return []


# ---------------------------------------------------------------------------
# K2 tests
# ---------------------------------------------------------------------------

def test_replay_forbids_crypto_paper_adapter():
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=FakeTransport())
    with pytest.raises(RuntimeError):
        safety.assert_replay_safe(adapter, declared_mode="REPLAY")


def test_replay_forbids_crypto_live_adapter():
    adapter = CryptoLiveBrokerAdapter(run_id="t", transport=FakeTransport())
    with pytest.raises(RuntimeError):
        safety.assert_replay_safe(adapter, declared_mode="REPLAY")


def test_paper_forbids_crypto_live(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    adapter = CryptoLiveBrokerAdapter(run_id="t", transport=FakeTransport())
    with pytest.raises(RuntimeError):
        safety.assert_paper_safe(adapter)


def test_paper_allows_crypto_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=FakeTransport())
    safety.assert_paper_safe(adapter)  # must not raise


def test_factory_paper_crypto(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=FakeTransport())
    assert isinstance(adapter, CryptoPaperBrokerAdapter)


def test_factory_live_crypto_requires_live_config(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    for k in ("LIVE_MAX_ORDER_SIZE", "LIVE_DAILY_LOSS_LIMIT", "LIVE_KILL_SWITCH", "LIVE_RISK_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="missing"):
        create_adapter("LIVE", run_id="t", venue="crypto", transport=FakeTransport())

    monkeypatch.setenv("LIVE_MAX_ORDER_SIZE", "1")
    monkeypatch.setenv("LIVE_DAILY_LOSS_LIMIT", "1000")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    monkeypatch.setenv("LIVE_RISK_ENABLED", "1")
    adapter = create_adapter("LIVE", run_id="t", venue="crypto", transport=FakeTransport())
    assert isinstance(adapter, CryptoLiveBrokerAdapter)


def test_factory_replay_unaffected_by_crypto_venue():
    with pytest.raises(ValueError):
        create_adapter("REPLAY", venue="crypto", run_id="t")


def test_counter_snapshot_includes_crypto():
    snap = safety.counter_snapshot()
    assert "crypto_order_call_count" in snap


def test_paper_guard_honors_declared_mode(monkeypatch):
    # ambient env is REPLAY; declared_mode="PAPER" must still trigger the live-adapter guard
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    adapter = CryptoLiveBrokerAdapter(transport=FakeTransport())
    with pytest.raises(RuntimeError):
        safety.assert_paper_safe(adapter, declared_mode="PAPER")


def test_live_config_honors_declared_mode(monkeypatch):
    # ambient env is REPLAY; declared_mode="LIVE" must still enforce required vars
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    for k in ("LIVE_MAX_ORDER_SIZE", "LIVE_DAILY_LOSS_LIMIT", "LIVE_KILL_SWITCH", "LIVE_RISK_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError):
        safety.assert_live_config(declared_mode="LIVE")


def test_factory_paper_crypto_guard_with_replay_env(monkeypatch):
    # ambient REPLAY: PAPER crypto is allowed; LIVE crypto must still raise
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    for k in ("LIVE_MAX_ORDER_SIZE", "LIVE_DAILY_LOSS_LIMIT", "LIVE_KILL_SWITCH", "LIVE_RISK_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=FakeTransport())
    assert isinstance(adapter, CryptoPaperBrokerAdapter)
    with pytest.raises(RuntimeError):
        create_adapter("LIVE", run_id="t", venue="crypto", transport=FakeTransport())
