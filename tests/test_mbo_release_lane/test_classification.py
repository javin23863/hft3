"""MBO lane classification tests."""

from __future__ import annotations

from mbo_release_lane.classification import classify_normalized_events
from mbo_release_lane.constants import LIFECYCLE_ACTIONS


def _sample_events(n: int = 5):
    out = []
    for i in range(1, n + 1):
        out.append(
            {
                "order_id": i,
                "action": "add",
                "side": "B",
                "price": "5500.0",
                "size": "10",
                "sequence_number": i,
                "exchange_timestamp": 1_000_000 + i,
            }
        )
    return out


def test_true_mbo_classification():
    result = classify_normalized_events(_sample_events())
    assert result.is_true_mbo
    assert not result.reject_reasons


def test_rejects_missing_order_id():
    events = _sample_events()
    events[2]["order_id"] = 0
    result = classify_normalized_events(events)
    assert not result.is_true_mbo
    assert any("order_id" in r for r in result.reject_reasons)


def test_rejects_mbp_like_stream():
    events = [
        {
            "order_id": 0,
            "action": "trade",
            "side": "B",
            "price": "100",
            "size": "1",
            "sequence_number": 1,
            "exchange_timestamp": 100,
        }
    ]
    result = classify_normalized_events(events)
    assert not result.is_true_mbo


def test_lifecycle_actions_defined():
    assert "add" in LIFECYCLE_ACTIONS
    assert "cancel" in LIFECYCLE_ACTIONS
