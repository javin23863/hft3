"""Minimum daily OHLCV calendar lookback derived from walk-forward + screener filters."""
from __future__ import annotations

from equities_lane.src.types import WalkForwardConfig


def required_daily_lookback_days(
    wf: WalkForwardConfig,
    *,
    rvol_trading_days: int = 20,
    consolidation_days: int = 20,
    min_folds: int = 3,
) -> int:
    """Calendar days of daily bars needed before session_date for backtest + screen."""
    fold_span = wf.train_days + wf.val_days + wf.test_days
    multi_fold = fold_span + max(0, min_folds - 1) * wf.step_days
    trading_buffer = max(rvol_trading_days, consolidation_days)
    calendar_trading = int(trading_buffer * 1.5) + 10
    return multi_fold + calendar_trading
