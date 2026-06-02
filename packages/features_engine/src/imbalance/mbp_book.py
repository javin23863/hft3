"""MBP-10 aggregated depth book (not order-level Level 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class MBPLevel:
    price: float
    size: int
    count: int = 0


@dataclass
class MBP10Book:
    """Incremental 10-level book from MBP-10 depth updates."""

    bids: Dict[float, MBPLevel] = field(default_factory=dict)
    asks: Dict[float, MBPLevel] = field(default_factory=dict)
    _best_bid: float = 0.0
    _best_ask: float = float("inf")

    def apply_level(
        self,
        side: str,
        price: float,
        size: int,
        *,
        count: int = 0,
    ) -> None:
        book = self.bids if side.upper() in ("B", "BID") else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = MBPLevel(price=price, size=size, count=count)
        self._refresh_bbo()

    def _refresh_bbo(self) -> None:
        self._best_bid = max(self.bids.keys()) if self.bids else 0.0
        self._best_ask = min(self.asks.keys()) if self.asks else float("inf")

    def get_best_bid(self) -> float:
        return self._best_bid

    def get_best_ask(self) -> float:
        return self._best_ask

    def top_k_depth(self, k: int) -> Tuple[int, int]:
        bid_prices = sorted(self.bids.keys(), reverse=True)[:k]
        ask_prices = sorted(self.asks.keys())[:k]
        bq = sum(self.bids[p].size for p in bid_prices)
        aq = sum(self.asks[p].size for p in ask_prices)
        return bq, aq

    def levels(self, k: int = 10) -> Tuple[List[Tuple[float, int]], List[Tuple[float, int]]]:
        bid_prices = sorted(self.bids.keys(), reverse=True)[:k]
        ask_prices = sorted(self.asks.keys())[:k]
        bids = [(p, self.bids[p].size) for p in bid_prices]
        asks = [(p, self.asks[p].size) for p in ask_prices]
        return bids, asks


def apply_mbp10_record(book: MBP10Book, record: dict) -> None:
    """Apply a normalized MBP-10 update dict {side, price, size, count?}."""
    book.apply_level(
        str(record.get("side", "B")),
        float(record["price"]),
        int(record.get("size", 0)),
        count=int(record.get("count", 0)),
    )
