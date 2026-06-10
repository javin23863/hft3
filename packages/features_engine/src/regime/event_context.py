"""
Maps timestamp t to event context E_t using events.csv windows (F_t only).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

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

        # Pure-nanosecond window table for the hot path: label and priority
        # precomputed, effective_date folded into an activation timestamp.
        # (effective_date <= ts.date() is equivalent to ts >= midnight UTC of
        # the effective date.)
        self._windows_ns: list[tuple[int, int, int, int, Optional[str]]] = []
        for effective_date, start, end, event_type, window_name in self._windows:
            # Empty event_type stays lazily-fatal: the error fires only when a
            # timestamp actually lands inside the bad window (matching
            # resolve()), signalled here by a None label.
            bad_type = not event_type or event_type.lower() == "nan"
            if effective_date is None:
                eff_ns = 0
            else:
                eff_dt = datetime(
                    effective_date.year,
                    effective_date.month,
                    effective_date.day,
                    tzinfo=timezone.utc,
                )
                eff_ns = int(eff_dt.timestamp() * 1e9)
            self._windows_ns.append(
                (
                    int(start.timestamp() * 1e9),
                    int(end.timestamp() * 1e9),
                    eff_ns,
                    context_priority(event_type) if not bad_type else 2**31,
                    None if bad_type else row_to_event_context(event_type, str(window_name)),
                )
            )
        # Constant-label interval cache for monotonic hot-path callers:
        # within [lo, hi) the resolved label cannot change.
        self._cache_lo_ns: int = 1
        self._cache_hi_ns: int = 0
        self._cache_label: str = "NORMAL"

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
        """Pure-integer resolve for the per-event hot path.

        Caches the label over the interval where it is constant; replay
        timestamps are (near-)monotonic, so this is O(1) amortized with a
        full O(windows) rescan only at window boundaries.
        """
        ts = int(timestamp_ns)
        if self._cache_lo_ns <= ts < self._cache_hi_ns:
            return self._cache_label

        best_priority: int | None = None
        best_label = "NORMAL"
        # Next timestamp at which any window's membership can change.
        next_boundary = 2**63 - 1
        for start_ns, end_ns, eff_ns, priority, label in self._windows_ns:
            active = eff_ns <= ts and start_ns <= ts <= end_ns
            if active:
                if label is None:
                    raise ValueError(
                        "events.csv row has empty event_type inside active window"
                    )
                if best_priority is None or priority < best_priority:
                    best_priority = priority
                    best_label = label
                if end_ns + 1 > ts:
                    next_boundary = min(next_boundary, end_ns + 1)
            else:
                for b in (start_ns, eff_ns, end_ns + 1):
                    if b > ts:
                        next_boundary = min(next_boundary, b)

        self._cache_lo_ns = ts
        self._cache_hi_ns = next_boundary
        self._cache_label = best_label
        return best_label
