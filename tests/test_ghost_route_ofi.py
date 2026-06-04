from models.ghost_route import compute_ofi


def test_ofi_confirmation_is_directional_not_standalone() -> None:
    previous = {
        "exchange_timestamp": 100,
        "local_receive_timestamp": 100,
        "sequence_number": 1,
        "instrument": "ES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 100,
        "best_ask_size": 100,
    }
    current = {
        "exchange_timestamp": 101,
        "local_receive_timestamp": 101,
        "sequence_number": 2,
        "instrument": "ES",
        "best_bid": 100.0,
        "best_ask": 100.25,
        "best_bid_size": 40,
        "best_ask_size": 100,
    }

    nofi = compute_ofi(previous, current)

    assert nofi == -0.3
    assert nofi < 0
