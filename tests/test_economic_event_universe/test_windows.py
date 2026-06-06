from datetime import datetime, timezone

from economic_event_universe.registry import default_snapshot_offsets
from economic_event_universe.windows import generate_snapshot_times, snapshot_offsets


def test_default_offsets_match_yaml():
    offs = snapshot_offsets()
    assert 10 in offs
    assert -10 in offs
    assert offs == default_snapshot_offsets()


def test_download_window_hft_release():
    from economic_event_universe.registry import default_download_window
    from economic_event_universe.windows import download_window

    assert default_download_window() == (-60, 10)
    assert download_window("CPI") == (-60, 10)
    assert download_window("FOMC_STATEMENT") == (-60, 10)
    assert download_window("PROP_FLATTEN_TOPSTEP") == (-60, 10)


def test_generate_snapshot_times_sorted():
    anchor = datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc)
    times = generate_snapshot_times(anchor)
    assert times == sorted(times)
    assert anchor in times
