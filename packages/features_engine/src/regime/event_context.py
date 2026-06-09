"""
Maps timestamp t to event context E_t using events.csv windows (F_t only).
O(log M) binary search instead of O(M) linear scan.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.events_parser import load_and_parse_events
from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import context_priority
from hft3_bootstrap import data_system_root


def _ns(dt: datetime) -> int:
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _ymd(d: object) -> int:
    if d is None or (isinstance(d, float) and np.isnan(d)):
        return 0
    s = str(d).strip()[:10]
    if not s:
        return 0
    return int(s.replace("-", ""))


class EventContextEngine:
    """Resolves E_t label for a UTC timestamp against parsed event windows.
    
    Uses binary search on pre-sorted arrays for O(log M) lookup.
    """

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
        df = df.reset_index(drop=True)

        sort_key = df["start_utc"].apply(_ns)
        df = df.iloc[sort_key.argsort()].reset_index(drop=True)

        self._start_ns = np.array([_ns(v) for v in df["start_utc"]], dtype=np.int64)
        self._end_ns = np.array([_ns(v) for v in df["end_utc"]], dtype=np.int64)
        self._effective_ymd = np.array([_ymd(v) for v in df.get("effective_date", pd.Series([None] * len(df)))], dtype=np.int32)
        self._event_types = df["event_type"].astype(str).tolist()
        self._window_names = df["window_name"].astype(str).tolist()

        for i, et in enumerate(self._event_types):
            if not et or et.lower() == "nan":
                self._event_types[i] = ""

    def resolve(self, ts_utc: datetime) -> str:
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        else:
            ts_utc = ts_utc.astimezone(timezone.utc)
        return self.resolve_ns(int(ts_utc.timestamp() * 1_000_000_000))

    def resolve_ns(self, timestamp_ns: int) -> str:
        n = len(self._start_ns)
        if n == 0:
            return "NORMAL"

        right = int(np.searchsorted(self._start_ns, timestamp_ns, side="right"))
        left = int(np.searchsorted(self._end_ns, timestamp_ns, side="left"))

        candidates = []
        ts_ymd = _ymd(date.fromtimestamp(timestamp_ns / 1e9).isoformat())

        for i in range(left, right):
            if self._effective_ymd[i] > 0 and self._effective_ymd[i] > ts_ymd:
                continue
            et = self._event_types[i]
            if not et:
                continue
            candidates.append(
                (
                    context_priority(et),
                    et,
                    self._window_names[i],
                )
            )

        if not candidates:
            return "NORMAL"

        candidates.sort(key=lambda x: x[0])
        event_type, window_name = candidates[0][1], candidates[0][2]
        return row_to_event_context(str(event_type), str(window_name))
