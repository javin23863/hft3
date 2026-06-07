from __future__ import annotations

import io
from datetime import date, datetime, timezone

from crypto_lane.src.ingest.binance_vision_pull import _stream_monthly_buckets


def _ms(day: date, hour: int = 0) -> int:
    ts = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return int(ts.timestamp() * 1000) + hour * 3_600_000


def test_stream_monthly_buckets_splits_days():
    day_a = date(2024, 4, 1)
    day_b = date(2024, 4, 2)
    lines = [
        "update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time",
    ]
    for i in range(1200):
        d = day_a if i < 600 else day_b
        ms = _ms(d)
        lines.append(f"{i},100,1,101,1,{ms},{ms}")
    buf = io.BytesIO("\n".join(lines).encode("utf-8"))

    buckets, days_in_archive, row_count = _stream_monthly_buckets(
        buf, "BTCUSDT", {day_a, day_b}
    )
    assert row_count == 1200
    assert days_in_archive == {day_a, day_b}
    assert len(buckets[day_a]) == 600
    assert len(buckets[day_b]) == 600


def test_stream_flags_partial_month():
    day_a = date(2024, 4, 1)
    lines = [
        "update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time",
    ]
    ms = _ms(day_a)
    lines.append(f"1,100,1,101,1,{ms},{ms}")
    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buckets, days_in_archive, row_count = _stream_monthly_buckets(
        buf, "BTCUSDT", {day_a, date(2024, 4, 2)}
    )
    assert row_count == 1
    assert days_in_archive == {day_a}
    assert date(2024, 4, 2) not in buckets
