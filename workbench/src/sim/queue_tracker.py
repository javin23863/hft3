"""Queue position proxy for ReplayRunner path (MVP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class QueueSnapshot:
    timestamp_ns: int
    side: str
    price: float
    our_qty: int
    depth_ahead: int
    fill_probability_proxy: float


class QueueTracker:
    """Log estimated queue rank at submit; fill prob = 1 / (1 + depth_ahead)."""

    def __init__(self) -> None:
        self.snapshots: List[QueueSnapshot] = []

    def record_submit(
        self,
        timestamp_ns: int,
        side: str,
        price: float,
        our_qty: int,
        depth_ahead: int,
    ) -> QueueSnapshot:
        proxy = 1.0 / (1.0 + max(depth_ahead, 0))
        snap = QueueSnapshot(
            timestamp_ns=timestamp_ns,
            side=side,
            price=price,
            our_qty=our_qty,
            depth_ahead=depth_ahead,
            fill_probability_proxy=proxy,
        )
        self.snapshots.append(snap)
        return snap

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [
            {
                "timestamp_ns": s.timestamp_ns,
                "side": s.side,
                "price": s.price,
                "our_qty": s.our_qty,
                "depth_ahead": s.depth_ahead,
                "fill_probability_proxy": s.fill_probability_proxy,
            }
            for s in self.snapshots
        ]
