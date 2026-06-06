"""MBO lane validation and blocker tests."""

from __future__ import annotations

from mbo_release_lane.blockers import BlockerCode
from mbo_release_lane.validate import validate_event_stream


def _valid_stream():
    events = []
    for i in range(1, 6):
        events.append(
            {
                "release_id": "CPI_2024_09_11_TIGHT",
                "symbol": "MES.v.0",
                "order_id": i,
                "action": "add",
                "side": "B",
                "price": str(5500.0 + i * 0.25),
                "size": "10",
                "sequence_number": i,
                "exchange_timestamp": 1_000_000_000 + i * 1000,
            }
        )
    return events


def test_valid_stream_passes():
    report, validation = validate_event_stream(_valid_stream())
    assert not report.blocked
    assert validation["replay_valid"] is True
    assert validation["blocker_status"] == "valid"


def test_missing_order_id_blocks():
    events = _valid_stream()
    events[1]["order_id"] = 0
    report, validation = validate_event_stream(events)
    assert report.blocked
    assert report.blockers[0].code == BlockerCode.MISSING_ORDER_ID
    assert validation["replay_valid"] is False


def test_sequence_gap_blocks():
    events = _valid_stream()
    # Skip sequence 4 — gap between 3 and 5
    events[3]["sequence_number"] = 5
    report, _ = validate_event_stream(events)
    assert report.blocked
    assert any(b.code == BlockerCode.SEQUENCE_GAP for b in report.blockers)
