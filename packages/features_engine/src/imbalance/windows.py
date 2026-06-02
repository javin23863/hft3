"""Event-time rolling windows for imbalance features (no bar aggregation)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional


DEFAULT_WINDOW_MS = (100, 250, 500, 1000, 5000)


def ms_to_ns(ms: int) -> int:
    return ms * 1_000_000


@dataclass
class TimedSample:
    ts_ns: int
    value: float


@dataclass
class EventTimeWindowBuffer:
    """Ring buffer of (ts_ns, value) with event-time expiry."""

    window_ns: int
    _samples: Deque[TimedSample] = field(default_factory=deque)
    _sum: float = 0.0

    def push(self, ts_ns: int, value: float) -> None:
        self._samples.append(TimedSample(ts_ns, value))
        self._sum += value
        self._expire(ts_ns)

    def _expire(self, now_ns: int) -> None:
        cutoff = now_ns - self.window_ns
        while self._samples and self._samples[0].ts_ns < cutoff:
            self._sum -= self._samples[0].value
            self._samples.popleft()

    def sum(self, now_ns: int) -> float:
        self._expire(now_ns)
        return self._sum

    def count(self, now_ns: int) -> int:
        self._expire(now_ns)
        return len(self._samples)


@dataclass
class MultiWindowAggregator:
    window_ms: List[int] = field(default_factory=lambda: list(DEFAULT_WINDOW_MS))
    _buffers: Dict[int, EventTimeWindowBuffer] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._buffers = {
            w: EventTimeWindowBuffer(window_ns=ms_to_ns(w)) for w in self.window_ms
        }

    @classmethod
    def from_config(cls, window_ms: Optional[Iterable[int]] = None) -> "MultiWindowAggregator":
        if window_ms is None:
            return cls()
        return cls(window_ms=list(window_ms))

    def push(self, ts_ns: int, value: float) -> None:
        for buf in self._buffers.values():
            buf.push(ts_ns, value)

    def sums(self, now_ns: int) -> Dict[str, float]:
        return {f"window_{w}ms": self._buffers[w].sum(now_ns) for w in self.window_ms}
