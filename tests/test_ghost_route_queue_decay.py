from models.ghost_route import GhostRouteConfig, compute_queue_decay


def test_bid_shadow_decay_requires_cancel_modify_without_trade() -> None:
    cfg = GhostRouteConfig(tau_decay_norm=0.4, tau_remaining=0.7, epsilon_trade=1.0)
    events = [
        {
            "exchange_timestamp": 100,
            "local_receive_timestamp": 101,
            "sequence_number": 1,
            "instrument": "ES",
            "event_type": "cancel",
            "side": "bid",
            "price": 100.0,
            "size": 45,
            "best_bid": 100.0,
            "best_ask": 100.25,
        },
        {
            "exchange_timestamp": 101,
            "local_receive_timestamp": 102,
            "sequence_number": 2,
            "instrument": "ES",
            "event_type": "modify",
            "side": "bid",
            "price": 100.0,
            "size": 20,
            "remaining_size": 5,
            "best_bid": 100.0,
            "best_ask": 100.25,
        },
        {
            "exchange_timestamp": 102,
            "local_receive_timestamp": 103,
            "sequence_number": 3,
            "instrument": "ES",
            "event_type": "add",
            "side": "bid",
            "price": 100.0,
            "size": 5,
            "best_bid": 100.0,
            "best_ask": 100.25,
        },
    ]

    metrics = compute_queue_decay(events, side="bid", initial_quantity=100, current_quantity=40, config=cfg)

    assert metrics.shadow_decay_event is True
    assert metrics.raw_queue_decay == 55
    assert metrics.shadow_decay == 55
    assert metrics.normalized_shadow_decay == 0.55
    assert metrics.remaining_queue_ratio == 0.4
