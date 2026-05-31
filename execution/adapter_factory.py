"""Factory for mode-aware execution adapters."""
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
) -> ExecutionAdapter:
    mode = (mode or safety.execution_mode()).upper()
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
        safety.assert_replay_safe(adapter)
        return adapter

    if mode == "PAPER":
        adapter = PaperBrokerAdapter(run_id=run_id)
        safety.assert_paper_safe(adapter)
        return adapter

    if mode == "LIVE":
        safety.assert_live_config()
        return LiveBrokerAdapter(run_id=run_id)

    raise ValueError(f"Unknown EXECUTION_MODE: {mode}")


def forbid_live_adapter_in_replay(adapter: ExecutionAdapter) -> None:
    if safety.execution_mode() == "REPLAY" and isinstance(adapter, LiveBrokerAdapter):
        raise RuntimeError("LiveBrokerAdapter forbidden in REPLAY mode")
