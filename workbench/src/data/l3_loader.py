"""L3 NPZ loader with gap detection and book reset events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, List, Optional

import numpy as np

from features_engine.src.features.mbo_features import MBOEvent, OrderBook
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events


@dataclass
class BookResetEvent:
    timestamp_ns: int
    reason: str
    gap_ns: int = 0


@dataclass
class LoaderReport:
    event_count: int = 0
    gap_count: int = 0
    duplicate_order_ids: int = 0
    monotonic_violations: int = 0
    resets: List[BookResetEvent] = field(default_factory=list)
    has_snapshot: bool = False


class L3Loader:
    """Wrap NPZ feed with sequence/timestamp quality checks."""

    def __init__(
        self,
        gap_threshold_ns: int = 5_000_000_000,
        require_snapshot_on_gap: bool = True,
    ):
        self.gap_threshold_ns = gap_threshold_ns
        self.require_snapshot_on_gap = require_snapshot_on_gap
        self.book = OrderBook()
        self.report = LoaderReport()

    def load(self, path: str) -> np.ndarray:
        raw = load_npz_events(path)
        self._scan(raw)
        return raw

    def _scan(self, raw: np.ndarray) -> None:
        seen_orders: set[int] = set()
        prev_ts: Optional[int] = None
        for row in raw:
            self.report.event_count += 1
            ts = int(row["local_ts"])
            oid = int(row["order_id"])
            if prev_ts is not None and ts < prev_ts:
                self.report.monotonic_violations += 1
            if oid in seen_orders and int(row["ev"]) & 0xFF == 1:  # ADD
                self.report.duplicate_order_ids += 1
            seen_orders.add(oid)
            if prev_ts is not None and ts - prev_ts > self.gap_threshold_ns:
                self.report.gap_count += 1
                reset = BookResetEvent(ts, "inter_arrival_gap", ts - prev_ts)
                self.report.resets.append(reset)
                if self.require_snapshot_on_gap and not self.report.has_snapshot:
                    raise ValueError(
                        f"Gap at {ts} without snapshot in manifest; fail-loud per workbench policy"
                    )
            prev_ts = ts

    def iter_events(self, raw: np.ndarray) -> Iterator[MBOEvent]:
        for ev in iter_mbo_events(raw):
            self.book.apply_event(ev)
            yield ev

    def mark_snapshot_available(self) -> None:
        self.report.has_snapshot = True
