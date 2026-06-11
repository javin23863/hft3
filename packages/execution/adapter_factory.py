"""Factory for mode-aware execution adapters; crypto branches auto-wire CryptoKillSwitch + TradeManagerRiskLayer via crypto_risk.build_crypto_risk_check when risk_check is None.

venue param selects the broker back-end for PAPER/LIVE modes.
venue="crypto" routes to CryptoPaperBrokerAdapter / CryptoLiveBrokerAdapter (Bitfinex).
Crypto LIVE is fail-closed: assert_live_config() is called before the adapter is constructed.
All other venue values fall through to the existing Rithmic adapters unchanged.
REPLAY always uses HftBacktestSimulatedExchangeAdapter regardless of venue.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from execution import safety
from execution.adapters.hftbacktest_simulated_exchange import HftBacktestSimulatedExchangeAdapter
from execution.adapters.live_broker import LiveBrokerAdapter
from execution.adapters.paper_broker import PaperBrokerAdapter
from execution.interfaces import ExecutionAdapter


def create_adapter(
    mode: Optional[str] = None,
    *,
    hbt: Any = None,
    run_id: str = "replay",
    latency_ms: float = 1.0,
    queue_model: str = "LogProbQueueModel2",
    asset_no: int = 0,
    venue: str = "rithmic",
    transport: Any = None,
    risk_check: Any = None,
) -> ExecutionAdapter:
    mode = (mode or safety.execution_mode()).upper()
    venue = (venue or "rithmic").lower()
    safety.reset_counters()

    if mode == "REPLAY":
        if hbt is None:
            raise ValueError("REPLAY mode requires hbt handle for HftBacktestSimulatedExchangeAdapter")
        adapter = HftBacktestSimulatedExchangeAdapter(
            hbt,
            run_id=run_id,
            latency_ms=latency_ms,
            queue_model=queue_model,
            asset_no=asset_no,
        )
        safety.assert_replay_safe(adapter, declared_mode=mode)
        return adapter

    if mode == "PAPER":
        if venue == "crypto":
            # Lazy import: keeps urllib/hmac out of every replay import path.
            from execution.adapters.crypto_broker import CryptoPaperBrokerAdapter
            adapter = CryptoPaperBrokerAdapter(run_id=run_id, transport=transport, risk_check=risk_check)
            if risk_check is None:
                # PAPER runs pre-trade risk identically to LIVE per PIPELINE.md §7.
                from execution.crypto_risk import CryptoKillSwitch, build_crypto_risk_check
                adapter._risk_check = build_crypto_risk_check(adapter, kill_switch=CryptoKillSwitch(), execution_mode=mode)
            safety.assert_paper_safe(adapter, declared_mode=mode)
            return adapter
        adapter = PaperBrokerAdapter(run_id=run_id)
        safety.assert_paper_safe(adapter, declared_mode=mode)
        return adapter

    if mode == "LIVE":
        if venue == "crypto":
            # Fail-closed: assert_live_config() must pass before adapter construction.
            safety.assert_live_config(declared_mode=mode)
            # Lazy import: keeps urllib/hmac out of every replay import path.
            from execution.adapters.crypto_broker import CryptoLiveBrokerAdapter
            adapter = CryptoLiveBrokerAdapter(run_id=run_id, transport=transport, risk_check=risk_check)
            if risk_check is None:
                from execution.crypto_risk import CryptoKillSwitch, build_crypto_risk_check
                adapter._risk_check = build_crypto_risk_check(adapter, kill_switch=CryptoKillSwitch(), execution_mode=mode)
            return adapter
        safety.assert_live_config(declared_mode=mode)
        return LiveBrokerAdapter(run_id=run_id)

    raise ValueError(f"Unknown EXECUTION_MODE: {mode}")


def forbid_live_adapter_in_replay(adapter: ExecutionAdapter) -> None:
    if safety.execution_mode() == "REPLAY" and isinstance(adapter, LiveBrokerAdapter):
        raise RuntimeError("LiveBrokerAdapter forbidden in REPLAY mode")
