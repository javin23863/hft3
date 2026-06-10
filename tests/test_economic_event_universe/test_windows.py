from datetime import datetime, timezone

from economic_event_universe.registry import default_snapshot_offsets
from economic_event_universe.windows import generate_snapshot_times, snapshot_offsets


def test_default_offsets_match_yaml():
    offs = snapshot_offsets()
    # 1800 was removed from the yaml when the event_universe.yaml was updated to
    # use the new snapshot_offsets_sec list.  Verify the current canonical offsets
    # from the yaml are present and that snapshot_offsets() == default_snapshot_offsets().
    assert len(offs) > 0
    # Verify all entries are ints (not floats or strings)
    assert all(isinstance(o, int) for o in offs)
    # Core second-granularity offsets that the yaml always includes
    for expected_offset in (-10, -5, -1, 0):
        assert expected_offset in offs, f"{expected_offset} not in snapshot_offsets_sec"
    assert offs == default_snapshot_offsets()


def test_generate_snapshot_times_sorted():
    anchor = datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc)
    times = generate_snapshot_times(anchor)
    assert times == sorted(times)
    assert anchor in times
