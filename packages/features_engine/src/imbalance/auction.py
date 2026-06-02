"""Auction imbalance — separate from continuous book imbalance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

AUCTION_WINDOW_PHASES: Set[str] = {
    "auction_pub",
    "open",
    "close",
    "halt_reopen",
    "rebalance",
}


@dataclass
class AuctionImbalanceEvent:
    auction_type: str
    imbalance_side: str
    paired_quantity: float
    total_imbalance_quantity: float
    market_imbalance_quantity: float
    indicative_price: float
    reference_price: float
    near_price: float
    far_price: float
    auction_status: str
    imbalance_update_timestamp_ns: int
    symbol: str = ""
    venue: str = ""

    @classmethod
    def from_record(cls, record: dict) -> "AuctionImbalanceEvent":
        return cls(
            auction_type=str(record.get("auction_type", record.get("rtype", "unknown"))),
            imbalance_side=str(record.get("imbalance_side", record.get("side", ""))),
            paired_quantity=float(record.get("paired_quantity", record.get("paired_qty", 0))),
            total_imbalance_quantity=float(
                record.get("total_imbalance_quantity", record.get("imbalance_qty", 0))
            ),
            market_imbalance_quantity=float(record.get("market_imbalance_quantity", 0)),
            indicative_price=float(record.get("indicative_price", record.get("price", 0))),
            reference_price=float(record.get("reference_price", 0)),
            near_price=float(record.get("near_price", 0)),
            far_price=float(record.get("far_price", 0)),
            auction_status=str(record.get("auction_status", record.get("status", ""))),
            imbalance_update_timestamp_ns=int(
                record.get("ts_ns", record.get("timestamp_ns", 0))
            ),
            symbol=str(record.get("symbol", "")),
            venue=str(record.get("venue", "")),
        )


@dataclass
class AuctionImbalanceSnapshot:
    feature_family: str = "auction_imbalance"
    auction_type: str = ""
    imbalance_side: str = ""
    paired_quantity: float = 0.0
    total_imbalance_quantity: float = 0.0
    market_imbalance_quantity: float = 0.0
    indicative_price: float = math.nan
    reference_price: float = math.nan
    near_price: float = math.nan
    far_price: float = math.nan
    auction_status: str = ""
    imbalance_change: float = 0.0
    imbalance_change_rate: float = 0.0
    paired_quantity_change: float = 0.0
    indicative_price_change: float = 0.0
    distance_from_reference_price: float = math.nan
    time_to_auction_ns: Optional[int] = None
    auction_pressure_score: float = math.nan
    event_window_id: str = ""
    window_phase: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_family": self.feature_family,
            "auction_type": self.auction_type,
            "imbalance_side": self.imbalance_side,
            "paired_quantity": self.paired_quantity,
            "total_imbalance_quantity": self.total_imbalance_quantity,
            "market_imbalance_quantity": self.market_imbalance_quantity,
            "indicative_price": self.indicative_price,
            "reference_price": self.reference_price,
            "near_price": self.near_price,
            "far_price": self.far_price,
            "auction_status": self.auction_status,
            "imbalance_change": self.imbalance_change,
            "imbalance_change_rate": self.imbalance_change_rate,
            "paired_quantity_change": self.paired_quantity_change,
            "indicative_price_change": self.indicative_price_change,
            "distance_from_reference_price": self.distance_from_reference_price,
            "time_to_auction_ns": self.time_to_auction_ns,
            "auction_pressure_score": self.auction_pressure_score,
            "event_window_id": self.event_window_id,
            "window_phase": self.window_phase,
        }


class AuctionImbalanceTracker:
    def __init__(self) -> None:
        self._prev: Optional[AuctionImbalanceEvent] = None
        self._prev_ts: int = 0

    def update(
        self,
        event: AuctionImbalanceEvent,
        *,
        window_phase: str,
        event_window_id: str = "",
        auction_cutoff_ns: Optional[int] = None,
    ) -> AuctionImbalanceSnapshot:
        if window_phase not in AUCTION_WINDOW_PHASES and window_phase not in (
            "during_event",
            "pre_event",
            "post_event",
        ):
            raise ValueError(
                f"auction imbalance only valid in auction windows; got {window_phase}"
            )
        snap = AuctionImbalanceSnapshot(
            auction_type=event.auction_type,
            imbalance_side=event.imbalance_side,
            paired_quantity=event.paired_quantity,
            total_imbalance_quantity=event.total_imbalance_quantity,
            market_imbalance_quantity=event.market_imbalance_quantity,
            indicative_price=event.indicative_price,
            reference_price=event.reference_price,
            near_price=event.near_price,
            far_price=event.far_price,
            auction_status=event.auction_status,
            event_window_id=event_window_id,
            window_phase=window_phase,
        )
        if self._prev is not None:
            dt_ns = max(1, event.imbalance_update_timestamp_ns - self._prev_ts)
            snap.imbalance_change = (
                event.total_imbalance_quantity - self._prev.total_imbalance_quantity
            )
            snap.imbalance_change_rate = snap.imbalance_change / (dt_ns / 1e9)
            snap.paired_quantity_change = event.paired_quantity - self._prev.paired_quantity
            snap.indicative_price_change = event.indicative_price - self._prev.indicative_price
        if event.reference_price > 0:
            snap.distance_from_reference_price = (
                event.indicative_price - event.reference_price
            ) / event.reference_price
        if auction_cutoff_ns is not None:
            snap.time_to_auction_ns = auction_cutoff_ns - event.imbalance_update_timestamp_ns
        denom = event.paired_quantity + abs(event.total_imbalance_quantity) + 1e-9
        snap.auction_pressure_score = event.total_imbalance_quantity / denom
        self._prev = event
        self._prev_ts = event.imbalance_update_timestamp_ns
        return snap


def is_auction_window_phase(phase: str) -> bool:
    return phase in AUCTION_WINDOW_PHASES
