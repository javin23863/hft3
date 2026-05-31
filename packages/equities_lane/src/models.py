"""Session tick/quote models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SessionTick:
    ts_ns: int
    bid_px: float
    bid_sz: int
    ask_px: float
    ask_sz: int
    trade_px: float | None = None
    trade_sz: int | None = None
    aggressor: str | None = None
    event: str = "quote"

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> SessionTick:
        return cls(
            ts_ns=int(row["ts_ns"]),
            bid_px=float(row.get("bid_px", 0.0)),
            bid_sz=int(row.get("bid_sz", 0)),
            ask_px=float(row.get("ask_px", 0.0)),
            ask_sz=int(row.get("ask_sz", 0)),
            trade_px=float(row["trade_px"]) if row.get("trade_px") is not None else None,
            trade_sz=int(row["trade_sz"]) if row.get("trade_sz") is not None else None,
            aggressor=row.get("aggressor"),
            event=str(row.get("event", "quote")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ns": self.ts_ns,
            "bid_px": self.bid_px,
            "bid_sz": self.bid_sz,
            "ask_px": self.ask_px,
            "ask_sz": self.ask_sz,
            "trade_px": self.trade_px,
            "trade_sz": self.trade_sz,
            "aggressor": self.aggressor,
            "event": self.event,
        }


@dataclass
class FloatRecord:
    symbol: str
    as_of_date: str
    float_shares: float
    outstanding_shares: float


@dataclass
class DailyBar:
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
