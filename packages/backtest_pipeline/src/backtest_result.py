"""
Canonical dataclasses for backtest fill records and per-hypothesis results.

Moved here from signal_backtester so that replay_matrix and all other
consumers can import them without pulling in the (deleted) fill-simulation
code that lived in SignalBacktester.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class FillRecord:
    timestamp_ns: int
    side: str
    exec_price: float
    # float, not int: crypto fills are fractional and int() would truncate
    # a 0.5-contract fill to zero in the ledger.
    qty: float
    hypothesis_id: int
    signal: float
    mid_at_exec: float


@dataclass
class BacktestResult:
    hypothesis_id: int
    net_pnl: float
    num_trades: int
    win_rate: float
    expectancy: float
    adverse_selection_ticks: float
    tail_loss: float
    fills: List[FillRecord] = field(default_factory=list)
