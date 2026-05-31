"""Replay time source (event-time, monotonic)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class ReplayClock:
    """Monotonic replay clock aligned to MBO / hbt event time."""

    start_ns: int = 0
    now_ns: int = 0
    seed: int = 0

    def advance_to(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.now_ns:
            return
        self.now_ns = int(timestamp_ns)
        if self.start_ns == 0:
            self.start_ns = self.now_ns

    def tick(self, step_ns: int) -> int:
        self.now_ns += int(step_ns)
        if self.start_ns == 0:
            self.start_ns = self.now_ns
        return self.now_ns


def deterministic_run_id(
    npz_path: str,
    latency_ms: float,
    queue_model: str,
    seed: int = 0,
) -> str:
    payload = f"{npz_path}|{latency_ms}|{queue_model}|{seed}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]
