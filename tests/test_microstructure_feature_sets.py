from __future__ import annotations

import pytest

from features_engine.feature_sets import (
    MICROSTRUCTURE_FEATURE_RECEIPTS,
    micro_price,
    microstructure_feature_packet,
    order_book_imbalance,
    queue_imbalance,
    vamp,
    weighted_depth_price,
)


def test_microstructure_features_empty_depth_returns_zeroes():
    packet = microstructure_feature_packet({"bids": [], "asks": []})

    assert packet == {
        "order_book_imbalance": 0.0,
        "queue_imbalance": 0.0,
        "micro_price": 0.0,
        "vamp": 0.0,
        "weighted_depth_price": 0.0,
    }
    assert MICROSTRUCTURE_FEATURE_RECEIPTS["hot_path_status"] == "research_only_not_feature_index"


def test_microstructure_features_balanced_book():
    snapshot = {"bids": [(99.0, 10.0)], "asks": [(101.0, 10.0)]}

    assert order_book_imbalance(snapshot) == pytest.approx(0.0)
    assert queue_imbalance(snapshot) == pytest.approx(0.0)
    assert micro_price(snapshot) == pytest.approx(100.0)
    assert vamp(snapshot) == pytest.approx(100.0)
    assert weighted_depth_price(snapshot) == pytest.approx(100.0)


def test_microstructure_features_one_sided_book():
    snapshot = {"bids": [{"price": 99.0, "qty": 7.0}], "asks": []}

    assert order_book_imbalance(snapshot) == pytest.approx(1.0)
    assert queue_imbalance(snapshot) == pytest.approx(1.0)
    assert micro_price(snapshot) == pytest.approx(0.0)
    assert vamp(snapshot) == pytest.approx(0.0)
    assert weighted_depth_price(snapshot, side="bids") == pytest.approx(99.0)


def test_microstructure_features_reject_non_positive_depth():
    snapshot = {"bids": [(99.0, 10.0)], "asks": [(101.0, 10.0)]}

    with pytest.raises(ValueError, match="depth must be positive"):
        order_book_imbalance(snapshot, depth=0)


def test_microstructure_features_reject_non_finite_levels():
    snapshot = {"bids": [(float("nan"), 10.0)], "asks": [(101.0, 10.0)]}

    with pytest.raises(ValueError, match="non-finite depth level"):
        micro_price(snapshot)


def test_microstructure_features_reject_non_positive_prices():
    zero_price = {"bids": [(0.0, 10.0)], "asks": [(101.0, 10.0)]}
    negative_price = {"bids": [(-99.0, 10.0)], "asks": [(101.0, 10.0)]}

    with pytest.raises(ValueError, match="non-positive depth price"):
        micro_price(zero_price)
    with pytest.raises(ValueError, match="non-positive depth price"):
        micro_price(negative_price)


def test_microstructure_features_skip_zero_quantity_levels():
    snapshot = {"bids": [(99.0, 0.0)], "asks": [(101.0, 10.0)]}

    assert micro_price(snapshot) == pytest.approx(0.0)
    assert order_book_imbalance(snapshot) == pytest.approx(-1.0)


def test_microstructure_features_reject_negative_quantity():
    snapshot = {"bids": [(99.0, -1.0)], "asks": [(101.0, 10.0)]}

    with pytest.raises(ValueError, match="negative depth quantity"):
        micro_price(snapshot)


def test_microstructure_feature_formulas_multi_level_depth():
    snapshot = {
        "bids": [(99.0, 10.0), (98.0, 20.0)],
        "asks": [(101.0, 5.0), (102.0, 15.0)],
    }

    assert order_book_imbalance(snapshot, depth=2) == pytest.approx((30.0 - 20.0) / 50.0)
    assert queue_imbalance(snapshot) == pytest.approx((10.0 - 5.0) / 15.0)
    assert micro_price(snapshot) == pytest.approx((101.0 * 10.0 + 99.0 * 5.0) / 15.0)
    assert vamp(snapshot, depth=2) == pytest.approx(
        ((101.0 * 10.0) + (99.0 * 5.0) + (102.0 * 20.0) + (98.0 * 15.0)) / 50.0
    )
    assert weighted_depth_price(snapshot, side="asks") == pytest.approx(
        ((101.0 * 5.0) + (102.0 * 15.0)) / 20.0
    )
