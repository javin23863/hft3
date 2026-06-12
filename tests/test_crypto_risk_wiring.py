"""C7 risk-wiring unit suite — CRYPTO_LIVE.md §8 row K8."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from execution import safety
from execution.adapters.crypto_broker import (
    CryptoRateLimitError,
    CryptoTransportError,
    CryptoPaperBrokerAdapter,
)
from execution.adapter_factory import create_adapter
from execution.crypto_risk import (
    CryptoKillSwitch,
    build_crypto_risk_check,
    crypto_risk_config,
    execute_kill_sequence,
)
from execution.interfaces import OrderEventType, OrderIntent, new_intent_id


# ---------------------------------------------------------------------------
# FakeTransport (same shape as test_crypto_submission_gate.py)
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self) -> None:
        self.order_new_calls: list[dict] = []
        self.order_cancel_calls: list = []
        self.cancel_all_calls: int = 0
        self._open_orders: list = []

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        return [0, "SUCCESS", None, None, [[None, None, body.get("cid"), None, None]]]

    def order_cancel(self, body: dict):
        self.order_cancel_calls.append(body)
        return [0, "SUCCESS"]

    def cancel_all(self):
        self.cancel_all_calls += 1
        return [0, "SUCCESS"]

    def open_orders(self):
        return self._open_orders


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _intent(symbol: str = "BTCUSDT", qty: float = 0.01, timestamp_ns: int | None = None) -> OrderIntent:
    return OrderIntent(
        intent_id=new_intent_id(),
        run_id="test",
        timestamp_ns=time.time_ns() if timestamp_ns is None else timestamp_ns,
        strategy_id="s",
        model_id="m",
        symbol=symbol,
        side="BUY",
        order_type="LIMIT",
        price=30_000.0,
        quantity=qty,
    )


# ---------------------------------------------------------------------------
# 1. Kill switch fired → blocks subsequent submits
# ---------------------------------------------------------------------------

def test_kill_switch_fired_blocks_submit(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=transport)

    # Pre-fire: must be ACCEPTED
    ev_pre = adapter.submit_order(_intent())
    assert ev_pre.event_type == OrderEventType.ORDER_ACCEPTED

    order_new_before = len(transport.order_new_calls)

    # Fire the kill switch
    CryptoKillSwitch().fire()

    # Post-fire: must be REJECTED; transport order_new count must not advance
    ev_post = adapter.submit_order(_intent())
    assert ev_post.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == order_new_before


# ---------------------------------------------------------------------------
# 2. Absent LIVE_KILL_SWITCH → fail-closed, submit blocked
# ---------------------------------------------------------------------------

def test_kill_switch_absent_env_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    transport = FakeTransport()
    safety.reset_counters()

    ks = CryptoKillSwitch()
    assert ks.status() == "fired"

    adapter = CryptoPaperBrokerAdapter(
        run_id="t",
        transport=transport,
        risk_check=build_crypto_risk_check(None, kill_switch=ks, execution_mode="PAPER"),
    )
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 0


# ---------------------------------------------------------------------------
# 3. Crypto symbol (BTCUSDT) passes eligibility
# ---------------------------------------------------------------------------

def test_risk_eligibility_crypto_symbol_passes(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=transport)
    ev = adapter.submit_order(_intent("BTCUSDT"))
    assert ev.event_type == OrderEventType.ORDER_ACCEPTED
    assert len(transport.order_new_calls) == 1


# ---------------------------------------------------------------------------
# 4. Foreign symbol (ES) → blocked by crypto risk config eligibility
# ---------------------------------------------------------------------------

def test_risk_eligibility_foreign_symbol_blocked(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=transport)
    ev = adapter.submit_order(_intent("ES"))
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 0


# ---------------------------------------------------------------------------
# 5. LIVE_MAX_ORDER_SIZE env → crypto_risk_config maps it; oversized order blocked
# ---------------------------------------------------------------------------

def test_live_env_limits_mapped(monkeypatch):
    monkeypatch.setenv("LIVE_MAX_ORDER_SIZE", "2")
    cfg = crypto_risk_config()
    assert cfg.max_order_size == 2.0

    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=transport)
    ev = adapter.submit_order(_intent("BTCUSDT", qty=3.0))
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 0


# ---------------------------------------------------------------------------
# 6. execute_kill_sequence: within_budget True; cancel_all called once
# ---------------------------------------------------------------------------

def test_execute_kill_sequence_budget(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)

    ks = CryptoKillSwitch()
    report = execute_kill_sequence(adapter, ks)

    assert report["fired"] is True
    assert report["cancel_ok"] is True
    assert report["within_budget"] is True
    assert report["pass"] is True
    assert transport.cancel_all_calls == 1


# ---------------------------------------------------------------------------
# 6b. execute_kill_sequence: cancel_all raises → cancel_ok False, pass False, fired True
# ---------------------------------------------------------------------------

class FakeTransportCancelFail:
    def __init__(self) -> None:
        self.order_new_calls: list[dict] = []
        self.cancel_all_calls: int = 0

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        return [0, "SUCCESS", None, None, [[None, None, body.get("cid"), None, None]]]

    def cancel_all(self):
        self.cancel_all_calls += 1
        raise CryptoTransportError("simulated cancel_all failure")

    def open_orders(self):
        return []


def test_kill_sequence_cancel_failure_fail_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransportCancelFail()
    adapter = CryptoPaperBrokerAdapter(run_id="t", transport=transport)

    ks = CryptoKillSwitch()
    report = execute_kill_sequence(adapter, ks)

    assert report["fired"] is True
    assert report["cancel_ok"] is False
    assert report["pass"] is False


# ---------------------------------------------------------------------------
# 7. Explicit risk_check=lambda i: False wins over auto-wiring
# ---------------------------------------------------------------------------

def test_explicit_risk_check_still_wins(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    # Passing explicit risk_check bypasses auto-wiring (factory branch skips auto-wire when risk_check is not None)
    adapter = create_adapter(
        "PAPER",
        run_id="t",
        venue="crypto",
        transport=transport,
        risk_check=lambda i: False,
    )
    ev = adapter.submit_order(_intent())
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert ev.rejection_reason == "risk_blocked"
    assert len(transport.order_new_calls) == 0


# ---------------------------------------------------------------------------
# 8. Drill script: --simulated exits 0, stdout JSON has "pass": true
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
def test_drill_script_simulated_exit0(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    packages_dir = str(repo_root / "packages")
    script = str(repo_root / "scripts" / "crypto_kill_drill.py")

    env = os.environ.copy()
    env["PYTHONPATH"] = packages_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["EXECUTION_MODE"] = "PAPER"
    env["LIVE_KILL_SWITCH"] = "armed"
    # Ensure no real keys leak into subprocess
    env.pop("HFT3_CRYPTO_BITFINEX_API_KEY", None)
    env.pop("HFT3_CRYPTO_BITFINEX_API_SECRET", None)

    result = subprocess.run(
        [sys.executable, script, "--simulated"],
        capture_output=True,
        text=True,
        env=env,
        timeout=25,
    )
    assert result.returncode == 0, (
        f"drill exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    output = json.loads(result.stdout.strip())
    assert output["pass"] is True, f"drill JSON not passing: {output}"


# ---------------------------------------------------------------------------
# 9. Zero-timestamp intent → fail-closed (STALE_SIGNAL from risk layer)
# ---------------------------------------------------------------------------

def test_zero_timestamp_intent_blocked(monkeypatch):
    # timestamp_ns=0 is treated as stale signal (age >> 50 ms threshold);
    # the auto-wired risk adapter must reject with ORDER_REJECTED (STALE_SIGNAL).
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "armed")
    transport = FakeTransport()
    safety.reset_counters()

    adapter = create_adapter("PAPER", run_id="t", venue="crypto", transport=transport)
    ev = adapter.submit_order(_intent(timestamp_ns=0))
    assert ev.event_type == OrderEventType.ORDER_REJECTED
    assert len(transport.order_new_calls) == 0
