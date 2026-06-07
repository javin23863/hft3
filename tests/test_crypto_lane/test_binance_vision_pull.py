from __future__ import annotations

from datetime import date

from crypto_lane.src.ingest.binance_vision_pull import (
    _months_for_days,
    vision_monthly_url,
)


def test_vision_monthly_url_matches_cae_convention():
    url = vision_monthly_url("BTCUSDT", 2024, 4)
    assert url == (
        "https://data.binance.vision/data/futures/um/monthly/bookTicker/"
        "BTCUSDT/BTCUSDT-bookTicker-2024-04.zip"
    )


def test_months_for_days_groups_contiguous_range():
    days = [date(2024, 4, 2), date(2024, 4, 30), date(2024, 5, 1)]
    grouped = _months_for_days(days)
    assert grouped == [
        (2024, 4, [date(2024, 4, 2), date(2024, 4, 30)]),
        (2024, 5, [date(2024, 5, 1)]),
    ]
