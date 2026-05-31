"""Query upcoming macro releases from events.csv with holiday-aware metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from data_system.src.events_parser import load_and_parse_events
from economic_event_universe.holidays import apply_holiday_adjustment
from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import get_event_def
from economic_event_universe.timezone import anchor_utc, to_user_tz
from hft3_bootstrap import data_system_root

__all__ = [
    "UpcomingEvent",
    "apply_holiday_adjustment",
    "list_upcoming",
    "resolve_release_datetime",
]


@dataclass(frozen=True)
class UpcomingEvent:
    event_id: str
    event_type: str
    release_date: str
    anchor_utc: datetime
    anchor_user_tz: datetime
    event_context: str
    source: str
    source_url: str
    window_name: str


def _events_csv_path() -> str:
    return str(data_system_root() / "config" / "events.csv")


def resolve_release_datetime(event_type: str, release_date: str) -> datetime:
    cfg = get_event_def(event_type)
    adj = apply_holiday_adjustment(event_type, date.fromisoformat(release_date))
    return anchor_utc(
        adj.isoformat(),
        cfg.get("anchor_time", "08:30:00"),
        cfg.get("timezone", "America/New_York"),
    )


def list_upcoming(
    user_tz: str = "Asia/Phnom_Penh",
    *,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    event_types: Optional[Iterable[str]] = None,
    events_csv: Optional[str] = None,
) -> list[UpcomingEvent]:
    """List releases from events.csv (anchors already holiday-adjusted by builder)."""
    df = load_and_parse_events(events_csv or _events_csv_path())
    today = from_date or date.today()
    end = to_date or date(today.year + 1, 12, 31)
    allowed = set(event_types) if event_types else None
    out: list[UpcomingEvent] = []
    for _, row in df.iterrows():
        et = str(row["event_type"])
        if allowed and et not in allowed:
            continue
        rd = str(row["release_date"])
        d = date.fromisoformat(rd)
        if d < today or d > end:
            continue
        anchor = row["anchor_utc"]
        if getattr(anchor, "tzinfo", None) is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        out.append(
            UpcomingEvent(
                event_id=str(row["event_id"]),
                event_type=et,
                release_date=rd,
                anchor_utc=anchor,
                anchor_user_tz=to_user_tz(anchor, user_tz),
                event_context=row_to_event_context(et, str(row["window_name"])),
                source=str(row.get("source", "")),
                source_url=str(row.get("source_url", "")),
                window_name=str(row["window_name"]),
            )
        )
    out.sort(key=lambda e: e.anchor_utc)
    return out
