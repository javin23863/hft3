"""Book imbalance from visible depth (MBO OrderBook or MBP-10 aggregated book)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Protocol


class DepthBook(Protocol):
    def top_k_depth(self, k: int) -> tuple[int, int]: ...
    def get_best_bid(self) -> float: ...
    def get_best_ask(self) -> float: ...


@dataclass
class BookImbalanceSnapshot:
    bid_size_l1: float
    ask_size_l1: float
    bid_size_l1_l3: float
    ask_size_l1_l3: float
    bid_size_l1_l5: float
    ask_size_l1_l5: float
    bid_size_l1_l10: float
    ask_size_l1_l10: float
    book_imbalance_l1: float
    book_imbalance_l3: float
    book_imbalance_l5: float
    book_imbalance_l10: float
    spread: float
    mid_price: float
    microprice: float
    book_state: str = "ok"
    quality_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "bid_size_l1": self.bid_size_l1,
            "ask_size_l1": self.ask_size_l1,
            "bid_size_l1_l3": self.bid_size_l1_l3,
            "ask_size_l1_l3": self.ask_size_l1_l3,
            "bid_size_l1_l5": self.bid_size_l1_l5,
            "ask_size_l1_l5": self.ask_size_l1_l5,
            "bid_size_l1_l10": self.bid_size_l1_l10,
            "ask_size_l1_l10": self.ask_size_l1_l10,
            "book_imbalance_l1": self.book_imbalance_l1,
            "book_imbalance_l3": self.book_imbalance_l3,
            "book_imbalance_l5": self.book_imbalance_l5,
            "book_imbalance_l10": self.book_imbalance_l10,
            "spread": self.spread,
            "mid_price": self.mid_price,
            "microprice": self.microprice,
            "book_state": self.book_state,
            "quality_flags": list(self.quality_flags),
        }


def book_imbalance_ratio(bid_size: float, ask_size: float) -> float:
    denom = bid_size + ask_size
    if denom <= 0:
        return math.nan
    return (bid_size - ask_size) / denom


def compute_microprice(bid_px: float, ask_px: float, bid_qty: float, ask_qty: float) -> float:
    denom = bid_qty + ask_qty
    if denom <= 0 or bid_px <= 0 or ask_px <= 0:
        return math.nan
    return (bid_px * ask_qty + ask_px * bid_qty) / denom


def compute_book_imbalance(book: DepthBook) -> BookImbalanceSnapshot:
    flags: list[str] = []
    b1, a1 = book.top_k_depth(1)
    b3, a3 = book.top_k_depth(3)
    b5, a5 = book.top_k_depth(5)
    b10, a10 = book.top_k_depth(10)

    bb = book.get_best_bid()
    ba = book.get_best_ask()
    state = "ok"
    if bb <= 0 or ba >= float("inf") or ba <= 0:
        state = "missing_depth"
        flags.append("missing_depth")
    elif ba < bb:
        state = "crossed"
        flags.append("crossed_book")
    elif ba == bb:
        state = "locked"
        flags.append("locked_book")

    spread = ba - bb if state == "ok" else math.nan
    mid = (bb + ba) / 2.0 if state == "ok" else math.nan
    micro = compute_microprice(bb, ba, float(b1), float(a1))

    def imb(b: float, a: float) -> float:
        if state != "ok":
            return math.nan
        if b < 0 or a < 0:
            flags.append("negative_depth")
            return math.nan
        return book_imbalance_ratio(b, a)

    return BookImbalanceSnapshot(
        bid_size_l1=float(b1),
        ask_size_l1=float(a1),
        bid_size_l1_l3=float(b3),
        ask_size_l1_l3=float(a3),
        bid_size_l1_l5=float(b5),
        ask_size_l1_l5=float(a5),
        bid_size_l1_l10=float(b10),
        ask_size_l1_l10=float(a10),
        book_imbalance_l1=imb(float(b1), float(a1)),
        book_imbalance_l3=imb(float(b3), float(a3)),
        book_imbalance_l5=imb(float(b5), float(a5)),
        book_imbalance_l10=imb(float(b10), float(a10)),
        spread=spread,
        mid_price=mid,
        microprice=micro,
        book_state=state,
        quality_flags=tuple(dict.fromkeys(flags)),
    )


def empty_book_snapshot() -> BookImbalanceSnapshot:
    nan = math.nan
    return BookImbalanceSnapshot(
        bid_size_l1=0.0,
        ask_size_l1=0.0,
        bid_size_l1_l3=0.0,
        ask_size_l1_l3=0.0,
        bid_size_l1_l5=0.0,
        ask_size_l1_l5=0.0,
        bid_size_l1_l10=0.0,
        ask_size_l1_l10=0.0,
        book_imbalance_l1=nan,
        book_imbalance_l3=nan,
        book_imbalance_l5=nan,
        book_imbalance_l10=nan,
        spread=nan,
        mid_price=nan,
        microprice=nan,
        book_state="empty",
        quality_flags=("empty_book",),
    )
