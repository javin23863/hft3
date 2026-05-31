"""Performance metrics for WFC matrix rows."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np


def sharpe_ratio(values: Sequence[float]) -> float:
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return float(arr.mean()) if arr.size else 0.0
    std = float(arr.std())
    if std < 1e-12:
        return 0.0
    return float(arr.mean() / std * np.sqrt(arr.size))


def profit_factor(wins: Sequence[float], losses: Sequence[float]) -> float:
    gross_win = sum(max(0.0, w) for w in wins)
    gross_loss = abs(sum(min(0.0, l) for l in losses))
    if gross_loss < 1e-12:
        return gross_win if gross_win > 0 else 0.0
    return gross_win / gross_loss


def max_drawdown(cumulative: Sequence[float]) -> float:
    if not cumulative:
        return 0.0
    arr = np.asarray(cumulative, dtype=float)
    peak = np.maximum.accumulate(arr)
    dd = arr - peak
    return float(dd.min())


def cagr(fractional_return: float, years: float) -> float:
    if years <= 0:
        return fractional_return
    base = 1.0 + fractional_return
    if base <= 0:
        return -1.0
    return float(base ** (1.0 / years) - 1.0)


def aggregate_event_metrics(
    event_results: List[Dict[str, Any]],
    *,
    years: float = 1.0,
    notional_capital: float = 100_000.0,
) -> Dict[str, float]:
    pnls = [float(e.get("net_pnl", 0.0)) for e in event_results]
    adj_pnls = [
        float(e.get("net_return_adjusted", e.get("net_pnl", 0.0))) for e in event_results
    ]
    trades = [int(e.get("num_trades", 0)) for e in event_results]
    total_pnl = sum(pnls)
    total_adj = sum(adj_pnls)
    total_trades = sum(trades)
    cum = np.cumsum(pnls).tolist() if pnls else [0.0]
    cum_adj = np.cumsum(adj_pnls).tolist() if adj_pnls else [0.0]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    trade_level = []
    for e in event_results:
        ntr = int(e.get("num_trades", 0))
        exp = float(e.get("expectancy", 0.0))
        if ntr > 0:
            trade_level.extend([exp] * ntr)
    if trade_level and len(set(trade_level)) > 1:
        sharpe = sharpe_ratio(trade_level)
    else:
        sharpe = sharpe_ratio(pnls)
    dd = max_drawdown(cum)
    dd_adj = max_drawdown(cum_adj)
    frac = total_pnl / notional_capital
    frac_adj = total_adj / notional_capital
    return {
        "net_return": total_pnl,
        "net_return_adjusted": total_adj,
        "sharpe": sharpe,
        "profit_factor": profit_factor(wins, losses),
        "cagr": cagr(frac, years),
        "cagr_adjusted": cagr(frac_adj, years),
        "max_drawdown": dd,
        "max_drawdown_adj_return": dd_adj,
        "trade_count": float(total_trades),
        "turnover": float(total_trades),
    }


def metric_value(metrics: Dict[str, float], name: str) -> float:
    aliases = {
        "max_drawdown_adj_return": "max_drawdown_adj_return",
        "risk_adjusted_return": "sharpe",
        "net_return_adjusted": "net_return_adjusted",
    }
    key = name if name in metrics else aliases.get(name, name)
    return float(metrics.get(key, 0.0))
