from datetime import datetime, timezone

from economic_event_universe.registry import default_snapshot_offsets
from economic_event_universe.windows import generate_snapshot_times, snapshot_offsets


def test_default_offsets_match_yaml():
    offs = snapshot_offsets()
    assert 1800 in offs
    assert offs == default_snapshot_offsets()


def test_generate_snapshot_times_sorted():
    anchor = datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc)
    times = generate_snapshot_times(anchor)
    assert times == sorted(times)
    assert anchor in times
