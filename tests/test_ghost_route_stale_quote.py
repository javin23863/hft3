from models.ghost_route.ghost_route_model import GhostRouteConfig, expected_edge_ticks, stale_quote_metrics


def test_short_signal_requires_micro_rich_stale_quote_and_depth() -> None:
    cfg = GhostRouteConfig(tau_z=1.0, min_depth_contracts=2, max_spread_ticks=1.0)
    macro = {
        "exchange_timestamp": 100,
        "local_receive_timestamp": 100,
        "sequence_number": 1,
        "instrument": "ES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 40,
        "best_ask_size": 100,
    }
    micro = {
        "exchange_timestamp": 100,
        "local_receive_timestamp": 100,
        "sequence_number": 2,
        "instrument": "MES",
        "best_bid": 102.0,
        "best_ask": 102.25,
        "best_bid_size": 3,
        "best_ask_size": 3,
    }

    metrics = stale_quote_metrics(
        macro_event=macro,
        micro_event=micro,
        macro_contract="ES",
        direction="SELL",
        config=cfg,
    )

    assert metrics.stale_quote is True
    assert metrics.available_depth == 3
    assert metrics.target_price == 102.0
    assert metrics.spread_zscore > cfg.tau_z
    assert expected_edge_ticks(direction="SELL", spread_zscore=metrics.spread_zscore, config=cfg) > 0
