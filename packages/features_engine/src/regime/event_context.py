"""
Maps timestamp t to event context E_t using events.csv windows (F_t only).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.events_parser import load_and_parse_events
from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import context_priority
from hft3_bootstrap import data_system_root


def _as_utc(value: datetime) -> datetime:
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _effective_date_value(effective_date: object) -> date | None:
    if effective_date is None or (isinstance(effective_date, float) and pd.isna(effective_date)):
        return None
    raw = str(effective_date).strip()[:10]
    if not raw:
        return None
    return date.fromisoformat(raw)


def _effective_date_active(effective_date: object, ts_utc: datetime) -> bool:
    if isinstance(effective_date, date):
        return effective_date <= ts_utc.date()
    if effective_date is None or (isinstance(effective_date, float) and pd.isna(effective_date)):
        return True
    raw = str(effective_date).strip()[:10]
    if not raw:
        return True
    return date.fromisoformat(raw) <= ts_utc.date()


class EventContextEngine:
    """Resolves E_t label for a UTC timestamp against parsed event windows."""

    def __init__(
        self,
        events_csv_path: str | None = None,
        *,
        event_id: str | None = None,
        event_type: str | None = None,
    ):
        path = events_csv_path or str(data_system_root() / "config" / "events.csv")
        df = load_and_parse_events(path)
        if event_id is not None:
            df = df[df["event_id"] == event_id]
            if df.empty:
                raise ValueError(f"event_id not in events.csv: {event_id}")
        elif event_type is not None:
            df = df[df["event_type"] == event_type]
            if df.empty:
                raise ValueError(f"event_type not in events.csv: {event_type}")
        self.events_df = df.reset_index(drop=True)
        self._windows = []
        for row in self.events_df.to_dict("records"):
            self._windows.append(
                (
                    _effective_date_value(row.get("effective_date")),
                    _as_utc(row["start_utc"]),
                    _as_utc(row["end_utc"]),
                    str(row["event_type"]).strip(),
                    str(row["window_name"]),
                )
            )

    def resolve(self, ts_utc: datetime) -> str:
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        else:
            ts_utc = ts_utc.astimezone(timezone.utc)

        candidates = []
        for effective_date, start, end, event_type, window_name in self._windows:
            if not _effective_date_active(effective_date, ts_utc):
                continue
            if start <= ts_utc <= end:
                if not event_type or event_type.lower() == "nan":
                    raise ValueError("events.csv row has empty event_type inside active window")
                candidates.append(
                    (
                        context_priority(event_type),
                        event_type,
                        window_name,
                    )
                )

        if not candidates:
            return "NORMAL"

        candidates.sort(key=lambda x: x[0])
        event_type, window_name = candidates[0][1], candidates[0][2]
        return row_to_event_context(str(event_type), str(window_name))

    def resolve_ns(self, timestamp_ns: int) -> str:
        ts = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
        return self.resolve(ts)
