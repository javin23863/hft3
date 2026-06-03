from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MBOAction(str, Enum):
    ADD = "ADD"
    CANCEL = "CANCEL"
    MODIFY = "MODIFY"
    EXECUTE = "EXECUTE"
    TRADE = "TRADE"
    FILL = "FILL"
    DELETE = "DELETE"
    SUSPEND = "SUSPEND"
    EXPIRED = "EXPIRED"
    AUCTION = "AUCTION"


class MBOSide(str, Enum):
    BID = "BID"
    ASK = "ASK"


class SessionState(str, Enum):
    PREMARKET = "PREMARKET"
    OPENING_CROSS = "OPENING_CROSS"
    CONTINUOUS = "CONTINUOUS"
    HALT = "HALT"
    CLOSING_CROSS = "CLOSING_CROSS"
    AFTER_HOURS = "AFTER_HOURS"


@dataclass
class MBOEvent:
    ts_event_ns: int
    ts_recv_ns: int
    seq_num: int
    order_id: int
    action: MBOAction
    side: MBOSide
    price: float
    size: int
    remaining_size: int
    order_age_ns: int
    queue_position: int
    price_level: int
    distance_from_mid: float
    distance_from_best: float
    venue: str
    session_state: SessionState
    auction_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_event_ns": self.ts_event_ns,
            "ts_recv_ns": self.ts_recv_ns,
            "seq_num": self.seq_num,
            "order_id": self.order_id,
            "action": self.action.value,
            "side": self.side.value,
            "price": self.price,
            "size": self.size,
            "remaining_size": self.remaining_size,
            "order_age_ns": self.order_age_ns,
            "queue_position": self.queue_position,
            "price_level": self.price_level,
            "distance_from_mid": self.distance_from_mid,
            "distance_from_best": self.distance_from_best,
            "venue": self.venue,
            "session_state": self.session_state.value,
            "auction_state": self.auction_state,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MBOEvent:
        return cls(
            ts_event_ns=int(d["ts_event_ns"]),
            ts_recv_ns=int(d.get("ts_recv_ns", d["ts_event_ns"])),
            seq_num=int(d.get("seq_num", 0)),
            order_id=int(d.get("order_id", 0)),
            action=MBOAction(d.get("action", "ADD")),
            side=MBOSide(d.get("side", "BID")),
            price=float(d.get("price", 0.0)),
            size=int(d.get("size", 0)),
            remaining_size=int(d.get("remaining_size", 0)),
            order_age_ns=int(d.get("order_age_ns", 0)),
            queue_position=int(d.get("queue_position", 0)),
            price_level=int(d.get("price_level", 0)),
            distance_from_mid=float(d.get("distance_from_mid", 0.0)),
            distance_from_best=float(d.get("distance_from_best", 0.0)),
            venue=str(d.get("venue", "")),
            session_state=SessionState(d.get("session_state", "CONTINUOUS")),
            auction_state=str(d.get("auction_state", "")),
        )


@dataclass
class AuctionImbalance:
    ts_ns: int
    paired_qty: int
    total_imbalance_qty: int
    imbalance_side: MBOSide
    near_clearing_price: float
    far_clearing_price: float
    reference_price: float
    session_state: SessionState

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ns": self.ts_ns,
            "paired_qty": self.paired_qty,
            "total_imbalance_qty": self.total_imbalance_qty,
            "imbalance_side": self.imbalance_side.value,
            "near_clearing_price": self.near_clearing_price,
            "far_clearing_price": self.far_clearing_price,
            "reference_price": self.reference_price,
            "session_state": self.session_state.value,
        }


@dataclass
class PriceLevel:
    price: float
    total_size: int
    order_count: int
    orders: list[OrderState]

    def __post_init__(self):
        if self.orders is None:
            self.orders = []


@dataclass
class OrderState:
    order_id: int
    side: MBOSide
    price: float
    size: int
    remaining_size: int
    add_ts_ns: int
    last_modify_ts_ns: int
    queue_position: int
    venue: str

    @property
    def age_ns(self) -> int:
        return self.last_modify_ts_ns - self.add_ts_ns
