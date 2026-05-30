"""Event metadata from data_system/config/events.csv."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from data_system.src.events_parser import load_and_parse_events


def load_event_row(event_id: str, events_csv: Path) -> dict[str, Any]:
    events = load_and_parse_events(str(events_csv))
    row = events[events["event_id"] == event_id]
    if row.empty:
        raise ValueError(f"event_id not found in events.csv: {event_id}")
    r = row.iloc[0]
    return {
        "event_id": event_id,
        "release_date": str(r["release_date"]),
        "release_time": str(r["release_time"]),
        "timezone": str(r["timezone"]),
        "window_name": str(r["window_name"]),
        "start_utc": r["start_utc"].isoformat(),
        "end_utc": r["end_utc"].isoformat(),
        "window_utc": f"{r['start_utc'].isoformat()} to {r['end_utc'].isoformat()}",
        "symbols": r["parsed_symbols"],
        "primary_symbol": r["parsed_symbols"][0],
    }
