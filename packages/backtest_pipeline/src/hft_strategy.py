"""hft_strategy — deprecation shim (mirrors signal_backtester.py).

The ``CombinedHypothesisStrategy`` that used to live here drove hftbacktest
directly (``hbt.submit_buy_order`` / ``hbt.submit_sell_order`` in its
``_submit``), bypassing the OrderIntent execution adapter. That direct-submit
lane produced order flow outside the audited lifecycle path: no OrderEvent
records, no lifecycle JSONL, no safety counters — numbers it produced could
disagree with certified ReplaySession evidence for the same inputs (see
runtime/audits/hft3_replay_execution_parity_audit.md).

The canonical replacement is ``CombinedHypothesisReplayStrategy`` in
``hypothesis_replay_strategy.py``: same signal aggregation (``max_abs`` /
``mean``), same MBO pipeline sync, but emits ``OrderIntent`` objects executed
through ``HftBacktestSimulatedExchangeAdapter`` inside ``ReplaySession``.

Importing the removed class name raises immediately instead of silently
resolving to a divergent implementation.
"""
from __future__ import annotations

from typing import Any

from backtest_pipeline.src.hypothesis_replay_strategy import (
    AggregateMode,
    CombinedHypothesisReplayStrategy,
)

__all__ = ["AggregateMode", "CombinedHypothesisReplayStrategy"]


def __getattr__(name: str) -> Any:
    if name == "CombinedHypothesisStrategy":
        raise ImportError(
            "CombinedHypothesisStrategy was removed: it submitted orders "
            "directly to hftbacktest, bypassing the OrderIntent adapter and "
            "lifecycle audit. Use CombinedHypothesisReplayStrategy from "
            "backtest_pipeline.src.hypothesis_replay_strategy with "
            "ReplaySession instead."
        )
    raise AttributeError(name)
