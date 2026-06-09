"""
signal_backtester — public dataclass re-exports only.

The internal fill-simulation that used to live here (mid-fill execution,
zero-slippage, 100 µs markout window, raw['px'] as mid proxy) was deleted
because it produced silently wrong results:

  - Markout horizon was int(0.1 * 1_000_000) = 100 µs, not 100 ms.
  - Markout and mid_price_plus_Nms columns read raw['px'] (the next MBO
    event's order price, not the mid).
  - All fills executed at mid with slippage_ticks=0 regardless of market
    impact.
  - FeeModel.calculate_trade_cost() defaulted tick_value=12.50 (ES
    contract) while this class was used for MES ($1.25/tick).

The canonical replacement is replay_matrix.py, which is backed by
ReplaySession (hftbacktest queue-model fills, real slippage, correct
timestamps).

BacktestResult and FillRecord are preserved here as re-exports from
backtest_result so that existing importers keep working without changes.
"""
from __future__ import annotations

# Re-export the canonical dataclasses so all existing importers continue to
# resolve `from backtest_pipeline.src.signal_backtester import BacktestResult`
# without modification.
from backtest_pipeline.src.backtest_result import BacktestResult, FillRecord

__all__ = ["BacktestResult", "FillRecord"]
