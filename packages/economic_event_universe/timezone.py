"""UTC anchors and user timezone conversion."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytz


def _zone(tz_name: str):
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return pytz.timezone(tz_name)


def anchor_utc(release_date: str, release_time: str, timezone_name: str) -> datetime:
    """Local release anchor -> UTC."""
    d = date.fromisoformat(release_date)
    parts = release_time.split(":")
    h, m = int(parts[0]), int(parts[1])
    sec = int(parts[2]) if len(parts) > 2 else 0
    local = datetime.combine(d, time(h, m, sec))
    tz = _zone(timezone_name)
    if hasattr(tz, "localize"):
        local = tz.localize(local)  # type: ignore[attr-defined]
    else:
        local = local.replace(tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC"))


def to_user_tz(dt_utc: datetime, tz_name: str) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
    return dt_utc.astimezone(_zone(tz_name))


def format_release_for_user(
    release_date: str,
    release_time: str,
    timezone_name: str,
    user_tz: str = "Asia/Phnom_Penh",
) -> str:
    utc = anchor_utc(release_date, release_time, timezone_name)
    local = to_user_tz(utc, user_tz)
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")
