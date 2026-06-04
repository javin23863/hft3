from models.ghost_route import GhostRouteConfig, GhostRouteModel, OrderIntent, simulate_fak_order


def test_fak_simulation_uses_total_latency_and_partial_fill() -> None:
    intent = OrderIntent(
        model="GHOST_ROUTE",
        timestamp_signal=1_000,
        macro_contract="ES",
        micro_contract="MES",
        direction="BUY",
        target_price=100.25,
        target_quantity=3,
        order_type="FAK_LIMIT",
        reason={"total_latency_us": 30},
    )
    arrival_book = {
        "exchange_timestamp": 1_030,
        "local_receive_timestamp": 1_030,
        "sequence_number": 10,
        "instrument": "MES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 10,
        "best_ask_size": 2,
    }

    fill = simulate_fak_order(intent, arrival_book)

    assert intent.timestamp_order_arrival == 1_030
    assert fill.fill_status == "PARTIAL_FILL"
    assert fill.filled_quantity == 2
    assert fill.fill_price == 100.25


def test_ghost_route_emits_research_order_intent_only_when_all_gates_pass() -> None:
    cfg = GhostRouteConfig(
        compute_latency_us=7,
        tau_decay_norm=0.4,
        tau_remaining=0.7,
        tau_ofi_norm=0.15,
        tau_z=1.0,
        min_expected_edge_ticks=0.25,
    )
    model = GhostRouteModel(cfg)
    previous_macro = {
        "exchange_timestamp": 100,
        "local_receive_timestamp": 100,
        "sequence_number": 1,
        "instrument": "ES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 100,
        "best_ask_size": 100,
    }
    current_macro = {
        "exchange_timestamp": 200,
        "local_receive_timestamp": 200,
        "sequence_number": 4,
        "instrument": "ES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 40,
        "best_ask_size": 100,
    }
    macro_window = [
        {
            "exchange_timestamp": 150,
            "local_receive_timestamp": 150,
            "sequence_number": 2,
            "instrument": "ES",
            "event_type": "cancel",
            "side": "bid",
            "price": 100.0,
            "size": 60,
            "best_bid": 100.0,
            "best_ask": 100.25,
        }
    ]
    micro = {
        "exchange_timestamp": 200,
        "local_receive_timestamp": 200,
        "sequence_number": 5,
        "instrument": "MES",
        "best_bid": 102.0,
        "best_ask": 102.25,
        "best_bid_size": 3,
        "best_ask_size": 3,
    }

    intent = model.evaluate(
        macro_contract="ES",
        macro_window_events=macro_window,
        previous_macro=previous_macro,
        current_macro=current_macro,
        current_micro=micro,
    )

    assert intent is not None
    assert intent.model == "GHOST_ROUTE"
    assert intent.direction == "SELL"
    assert intent.order_type == "FAK_LIMIT"
    assert intent.timestamp_order_arrival == 230
    assert intent.reason["expected_edge_ticks"] >= cfg.min_expected_edge_ticks
