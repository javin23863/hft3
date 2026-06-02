"""Sample imbalance snapshots during replay for artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SnapshotCollector:
    max_samples: int = 500
    stride: int = 100
    _samples: List[Dict[str, Any]] = field(default_factory=list, init=False)
    _event_count: int = 0

    def maybe_record(self, snap: Optional[Dict[str, Any]]) -> None:
        if snap is None:
            return
        self._event_count += 1
        if len(self._samples) >= self.max_samples:
            return
        if snap.get("auction"):
            self._samples.append(snap)
            return
        if self._event_count % self.stride != 0 and self._samples:
            return
        self._samples.append(snap)

    @property
    def samples(self) -> List[Dict[str, Any]]:
        return list(self._samples)

    def summarize(self) -> Dict[str, Any]:
        if not self._samples:
            return {"sample_count": 0}
        last = self._samples[-1]
        book = last.get("book") or {}
        of = last.get("order_flow") or {}
        return {
            "sample_count": len(self._samples),
            "events_seen": self._event_count,
            "last_book_imbalance_l1": book.get("book_imbalance_l1"),
            "last_book_imbalance_l10": book.get("book_imbalance_l10"),
            "last_ofi_l1": of.get("ofi_l1"),
            "last_signed_trade_pressure": of.get("signed_trade_pressure"),
        }
