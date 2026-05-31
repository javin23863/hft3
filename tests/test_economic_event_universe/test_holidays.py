from datetime import date

from economic_event_universe.holidays import apply_holiday_adjustment, is_federal_holiday


def test_thanksgiving_is_holiday():
    d = date(2024, 11, 28)
    assert is_federal_holiday(d)


def test_claims_shift_off_holiday_thursday():
    # Christmas 2025 is Thursday
    th = date(2025, 12, 25)
    assert th.weekday() == 3
    adj = apply_holiday_adjustment("UNEMPLOYMENT_CLAIMS", th)
    assert adj == date(2025, 12, 24)
