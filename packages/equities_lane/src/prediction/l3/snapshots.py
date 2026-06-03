from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .event_types import MBOAction, MBOEvent, MBOSide, SessionState
from .features import L3FeatureExtractor, L3Features
from .queue_state import BookSnapshot, OrderBookReconstructor


class L3SnapshotType(str, Enum):
    EVENT_WINDOW = "event_window"
    NANO_BURST = "nano_burst"
    PREMARKET_BUILDUP = "premarket_buildup"
    OPENING_IGNITION = "opening_ignition"
    INTRADAY_CONTINUATION = "intraday_continuation"
    HALT_REOPEN = "halt_reopen"
    AFTERHOURS_ACCUMULATION = "afterhours_accumulation"


@dataclass
class L3Snapshot:
    symbol: str
    timestamp_start_ns: int
    timestamp_end_ns: int
    snapshot_type: L3SnapshotType
    event_count: int
    wall_clock_duration_ns: int
    best_bid: float
    best_ask: float
    midprice: float
    microprice: float
    spread: float
    top_1_depth: int
    top_3_depth: int
    top_5_depth: int
    top_10_depth: int
    order_book_imbalance: float
    trade_sign_imbalance: float
    aggressive_buy_ratio: float
    ask_depletion_ratio: float
    ask_replenishment_failure: float
    bid_support_pressure: float
    cancel_asymmetry: float
    depth_vacuum_score: float
    queue_collapse_score: float
    event_acceleration: float
    book_resilience_score: float
    auction_pressure: float
    venue_pressure: float
    features: L3Features

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp_start_ns": self.timestamp_start_ns,
            "timestamp_end_ns": self.timestamp_end_ns,
            "snapshot_type": self.snapshot_type.value,
            "event_count": self.event_count,
            "wall_clock_duration_ns": self.wall_clock_duration_ns,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "midprice": self.midprice,
            "microprice": self.microprice,
            "spread": self.spread,
            "top_1_depth": self.top_1_depth,
            "top_3_depth": self.top_3_depth,
            "top_5_depth": self.top_5_depth,
            "top_10_depth": self.top_10_depth,
            "order_book_imbalance": self.order_book_imbalance,
            "trade_sign_imbalance": self.trade_sign_imbalance,
            "aggressive_buy_ratio": self.aggressive_buy_ratio,
            "ask_depletion_ratio": self.ask_depletion_ratio,
            "ask_replenishment_failure": self.ask_replenishment_failure,
            "bid_support_pressure": self.bid_support_pressure,
            "cancel_asymmetry": self.cancel_asymmetry,
            "depth_vacuum_score": self.depth_vacuum_score,
            "queue_collapse_score": self.queue_collapse_score,
            "event_acceleration": self.event_acceleration,
            "book_resilience_score": self.book_resilience_score,
            "auction_pressure": self.auction_pressure,
            "venue_pressure": self.venue_pressure,
            "features": self.features.to_dict(),
        }


WALL_CLOCK_WINDOWS_NS = [
    1_000_000,
    10_000_000,
    100_000_000,
    500_000_000,
    1_000_000_000,
    5_000_000_000,
    15_000_000_000,
    30_000_000_000,
    60_000_000_000,
    300_000_000_000,
    900_000_000_000,
    1_800_000_000_000,
]

EVENT_COUNT_WINDOWS = [10, 50, 100, 500, 1_000, 5_000, 10_000]


class L3SnapshotBuilder:
    def __init__(self, symbol: str):
        self._symbol = symbol
        self._reconstructor = OrderBookReconstructor()
        self._events: list[MBOEvent] = []
        self._snapshots: list[BookSnapshot] = []

    def add_event(self, event: MBOEvent):
        self._events.append(event)
        snap = self._reconstructor.process_event(event)
        if snap:
            self._snapshots.append(snap)

    def build_wall_clock_snapshot(
        self,
        window_ns: int,
        snapshot_type: L3SnapshotType = L3SnapshotType.EVENT_WINDOW,
    ) -> L3Snapshot | None:
        if not self._events or not self._snapshots:
            return None

        ts_end = self._events[-1].ts_event_ns
        ts_start = ts_end - window_ns

        window_events = [e for e in self._events if e.ts_event_ns >= ts_start]
        window_snapshots = [s for s in self._snapshots if s.ts_ns >= ts_start]

        if not window_events or not window_snapshots:
            return None

        extractor = L3FeatureExtractor(window_ns=window_ns)
        features = extractor.extract(window_events, window_snapshots)

        last_snap = window_snapshots[-1]
        return self._build_snapshot(
            ts_start,
            ts_end,
            snapshot_type,
            window_events,
            last_snap,
            features,
        )

    def build_event_count_snapshot(
        self,
        n_events: int,
        snapshot_type: L3SnapshotType = L3SnapshotType.EVENT_WINDOW,
    ) -> L3Snapshot | None:
        if len(self._events) < n_events or len(self._snapshots) < n_events:
            return None

        window_events = self._events[-n_events:]
        ts_start = window_events[0].ts_event_ns
        ts_end = window_events[-1].ts_event_ns
        window_ns = ts_end - ts_start

        window_snapshots = [s for s in self._snapshots if s.ts_ns >= ts_start]

        if not window_snapshots:
            return None

        extractor = L3FeatureExtractor(window_ns=window_ns)
        features = extractor.extract(window_events, window_snapshots)

        last_snap = window_snapshots[-1]
        return self._build_snapshot(
            ts_start,
            ts_end,
            snapshot_type,
            window_events,
            last_snap,
            features,
        )

    def build_multi_resolution_snapshots(
        self,
        snapshot_type: L3SnapshotType = L3SnapshotType.EVENT_WINDOW,
    ) -> list[L3Snapshot]:
        snapshots = []

        for window_ns in WALL_CLOCK_WINDOWS_NS:
            snap = self.build_wall_clock_snapshot(window_ns, snapshot_type)
            if snap:
                snapshots.append(snap)

        for n_events in EVENT_COUNT_WINDOWS:
            snap = self.build_event_count_snapshot(n_events, snapshot_type)
            if snap:
                snapshots.append(snap)

        return snapshots

    def _build_snapshot(
        self,
        ts_start: int,
        ts_end: int,
        snapshot_type: L3SnapshotType,
        events: list[MBOEvent],
        book_snap: BookSnapshot,
        features: L3Features,
    ) -> L3Snapshot:
        buy_trades = sum(1 for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE) and e.side == MBOSide.BID)
        sell_trades = sum(1 for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE) and e.side == MBOSide.ASK)
        total_trades = buy_trades + sell_trades
        trade_sign_imbalance = (buy_trades - sell_trades) / total_trades if total_trades > 0 else 0.0

        return L3Snapshot(
            symbol=self._symbol,
            timestamp_start_ns=ts_start,
            timestamp_end_ns=ts_end,
            snapshot_type=snapshot_type,
            event_count=len(events),
            wall_clock_duration_ns=ts_end - ts_start,
            best_bid=book_snap.best_bid,
            best_ask=book_snap.best_ask,
            midprice=book_snap.midprice,
            microprice=book_snap.microprice,
            spread=book_snap.spread,
            top_1_depth=book_snap.total_ask_depth_1,
            top_3_depth=book_snap.total_ask_depth_3,
            top_5_depth=book_snap.total_ask_depth_5,
            top_10_depth=book_snap.total_ask_depth_10,
            order_book_imbalance=book_snap.order_book_imbalance,
            trade_sign_imbalance=trade_sign_imbalance,
            aggressive_buy_ratio=features.aggressive_buy_ratio,
            ask_depletion_ratio=features.ask_depletion_ratio,
            ask_replenishment_failure=features.ask_replenishment_failure,
            bid_support_pressure=features.bid_support_pressure,
            cancel_asymmetry=features.cancel_asymmetry,
            depth_vacuum_score=features.depth_vacuum_score,
            queue_collapse_score=features.queue_collapse_score,
            event_acceleration=features.event_acceleration,
            book_resilience_score=1.0 - features.book_resilience_decay,
            auction_pressure=features.auction_pressure,
            venue_pressure=0.0,
            features=features,
        )

    def reset(self):
        self._reconstructor = OrderBookReconstructor()
        self._events.clear()
        self._snapshots.clear()
