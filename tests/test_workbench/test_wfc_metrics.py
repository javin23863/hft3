"""WFC metrics aggregation."""

from __future__ import annotations

from workbench.src.robustness.wfc.metrics import aggregate_event_metrics, max_drawdown


def test_max_drawdown_adj_return_matches_adj_curve():
    events = [
        {"net_pnl": 100.0, "net_return_adjusted": 80.0, "num_trades": 1},
        {"net_pnl": -50.0, "net_return_adjusted": -60.0, "num_trades": 1},
    ]
    m = aggregate_event_metrics(events)
    cum_adj = [80.0, 20.0]
    assert m["max_drawdown_adj_return"] == max_drawdown(cum_adj)
