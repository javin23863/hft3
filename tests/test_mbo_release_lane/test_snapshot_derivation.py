"""Post-replay snapshot derivation — 0_pre vs 0_post."""

from __future__ import annotations

from mbo_release_lane.snapshot_derivation import derive_snapshots_from_events


def _events_for_snapshots():
    ts0 = 1_000_000_000_000
    events = [
        {
            "order_id": 1,
            "action": "add",
            "side": "B",
            "price": "5500.0",
            "size": "10",
            "sequence_number": 1,
            "exchange_timestamp": ts0 - 500_000_000,
        },
        {
            "order_id": 2,
            "action": "add",
            "side": "A",
            "price": "5501.0",
            "size": "8",
            "sequence_number": 2,
            "exchange_timestamp": ts0 - 100_000_000,
        },
        {
            "order_id": 3,
            "action": "add",
            "side": "B",
            "price": "5500.5",
            "size": "5",
            "sequence_number": 3,
            "exchange_timestamp": ts0 + 100_000,
        },
    ]
    return events, ts0


def test_zero_pre_and_post_are_separate_labels():
    events, release_ts = _events_for_snapshots()
    snaps = derive_snapshots_from_events(events, release_timestamp_ns=release_ts)
    labels = [s.label for s in snaps]
    assert "0_pre" in labels
    assert "0_post" in labels
    pre = next(s for s in snaps if s.label == "0_pre")
    post = next(s for s in snaps if s.label == "0_post")
    assert pre.is_pre_release
    assert not post.is_pre_release
    assert pre.timestamp_ns < release_ts or pre.timestamp_ns == release_ts - 1


def test_pre_release_snapshots_flagged():
    events, release_ts = _events_for_snapshots()
    snaps = derive_snapshots_from_events(events, release_timestamp_ns=release_ts)
    for s in snaps:
        if s.label in ("-10s", "-5s", "-1s", "0_pre"):
            assert s.is_pre_release
