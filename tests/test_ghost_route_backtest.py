import pytest

from models.ghost_route.ghost_route_backtest import run_backtest
from models.ghost_route.ghost_route_model import GhostRouteConfig


def test_backtest_uses_ns_latency_and_computes_markout_economics() -> None:
    cfg = GhostRouteConfig(
        timestamp_unit="ns",
        compute_latency_us=7,
        tau_decay_norm=0.4,
        tau_remaining=0.7,
        tau_ofi_norm=0.15,
        tau_z=1.0,
        min_expected_edge_ticks=0.25,
        fees_ticks=0.05,
        estimated_slippage_ticks=0.05,
        adverse_selection_penalty_ticks=0.05,
    )
    macro = [
        {
            "exchange_timestamp": 100_000,
            "local_receive_timestamp": 100_000,
            "sequence_number": 1,
            "instrument": "ES",
            "best_bid": 100.0,
            "best_ask": 100.25,
            "best_bid_size": 100,
            "best_ask_size": 100,
        },
        {
            "exchange_timestamp": 150_000,
            "local_receive_timestamp": 150_000,
            "sequence_number": 2,
            "instrument": "ES",
            "event_type": "cancel",
            "side": "bid",
            "price": 100.0,
            "size": 60,
            "best_bid": 100.0,
            "best_ask": 100.25,
            "best_bid_size": 100,
            "best_ask_size": 100,
        },
        {
            "exchange_timestamp": 200_000,
            "local_receive_timestamp": 200_000,
            "sequence_number": 3,
            "instrument": "ES",
            "best_bid": 100.0,
            "best_ask": 100.25,
            "best_bid_size": 40,
            "best_ask_size": 100,
        },
    ]
    micro = [
        {
            "exchange_timestamp": 200_000,
            "local_receive_timestamp": 200_000,
            "sequence_number": 1,
            "instrument": "MES",
            "best_bid": 102.0,
            "best_ask": 102.25,
            "best_bid_size": 3,
            "best_ask_size": 3,
        },
        {
            "exchange_timestamp": 230_000,
            "local_receive_timestamp": 230_000,
            "sequence_number": 2,
            "instrument": "MES",
            "best_bid": 102.0,
            "best_ask": 102.25,
            "best_bid_size": 3,
            "best_ask_size": 3,
        },
        {
            "exchange_timestamp": 480_000,
            "local_receive_timestamp": 480_000,
            "sequence_number": 3,
            "instrument": "MES",
            "best_bid": 101.5,
            "best_ask": 101.75,
            "best_bid_size": 3,
            "best_ask_size": 3,
        },
    ]

    summary = run_backtest(macro, micro, macro_contract="ES", config=cfg)
    row = summary["event_log"][0]

    assert row["timestamp_order_arrival"] == 230_000
    assert row["lead_time_us"] == 30.0
    assert row["filled_quantity"] == 1
    assert row["markout_250us"] == 1.5
    assert row["gross_pnl"] == 1.5
    assert row["fees"] == 0.05
    assert row["slippage_cost"] == 0.05
    assert row["adverse_selection_cost"] == 0.05
    assert row["net_pnl"] == pytest.approx(1.35)
