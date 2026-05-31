from datetime import datetime, timezone

from economic_event_universe.timezone import anchor_utc, to_user_tz


def test_fomc_et_to_utc_winter():
    dt = anchor_utc("2024-01-31", "14:00:00", "America/New_York")
    assert dt == datetime(2024, 1, 31, 19, 0, tzinfo=timezone.utc)


def test_fomc_et_to_ict():
    dt = anchor_utc("2024-01-31", "14:00:00", "America/New_York")
    local = to_user_tz(dt, "Asia/Phnom_Penh")
    assert local.hour == 2 and local.day == 1


def test_phnom_penh_no_dst_shift():
    dt = anchor_utc("2024-07-04", "08:30:00", "America/New_York")
    july = to_user_tz(dt, "Asia/Phnom_Penh")
    jan = to_user_tz(anchor_utc("2024-01-04", "08:30:00", "America/New_York"), "Asia/Phnom_Penh")
    assert july.utcoffset() == jan.utcoffset()
