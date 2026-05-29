"""
Maps timestamp t to event context E_t using events.csv windows (F_t only).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.events_parser import load_and_parse_events


class EventContextEngine:
    """Resolves E_t label for a UTC timestamp against parsed event windows."""

    def __init__(self, events_csv_path: str | None = None):
        path = events_csv_path or str(_REPO_ROOT / "data_system" / "config" / "events.csv")
        self.events_df = load_and_parse_events(path)

    def resolve(self, ts_utc: datetime) -> str:
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        else:
            ts_utc = ts_utc.astimezone(timezone.utc)

        candidates = []
        for _, row in self.events_df.iterrows():
            start = row["start_utc"]
            end = row["end_utc"]
            if getattr(start, "tzinfo", None) is None:
                start = start.replace(tzinfo=timezone.utc)
            if getattr(end, "tzinfo", None) is None:
                end = end.replace(tzinfo=timezone.utc)
            if start <= ts_utc <= end:
                candidates.append(
                    (int(row.get("priority", 99)), row["event_type"], row["window_name"])
                )

        if not candidates:
            return "NORMAL"

        candidates.sort(key=lambda x: x[0])
        event_type, window_name = candidates[0][1], candidates[0][2]

        if window_name == "TIGHT":
            if event_type == "CPI":
                return "CPI_TIGHT"
            if event_type == "NFP":
                return "NFP_TIGHT"
            if "FOMC" in str(event_type):
                return "FOMC_STATEMENT_TIGHT"
        if event_type == "PROP_REOPEN":
            return "PROP_REOPEN"
        if event_type == "CASH_EQUITY_OPEN" or "OPEN" in str(event_type):
            return "CASH_EQUITY_OPEN"
        if event_type == "PROP_FLATTEN_TOPSTEP":
            return "PROP_FLATTEN_TOPSTEP"
        if "FRIDAY" in str(event_type):
            return "FRIDAY_CLOSE"
        if "APEX" in str(event_type):
            return "APEX_FLATTEN"
        if "TPT" in str(event_type) or "MyFunded" in str(event_type):
            return "TPT_FLATTEN"
        if "NEWS" in str(event_type):
            return "NEWS_RESTRICTION"

        return str(event_type)

    def resolve_ns(self, timestamp_ns: int) -> str:
        ts = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
        return self.resolve(ts)
