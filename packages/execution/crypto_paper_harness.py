"""C8 paper harness (CRYPTO_LIVE.md §5) — drives the crypto adapter against a Bitfinex
paper sub-account, records REAL paired submit/ack timestamps (perf_counter_ns at wire
boundaries, captured inside the adapter as LatencySample), writes
runtime/crypto_latency/latency_summary.json with measured=true ONLY at n>=1000
(LATENCY.md §10.3). No synthetic timestamps ever enter the authoritative summary.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from execution.adapters.crypto_broker import LatencySample
from execution.interfaces import OrderIntent, new_intent_id

# LATENCY.md §10.3 gate: fewer than this many accepted real pairs → measured stays False.
MIN_AUTHORITATIVE_SAMPLES = 1000
# ALPHA_CRYPTO.md C8 first-100-pairs gate.
FIRST_BATCH_TARGET = 100


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (no numpy). p in [0, 100].

    Uses nearest-rank method: rank = ceil(p/100 * n), 1-indexed.
    Empty list returns 0.0.
    """
    if not values:
        return 0.0
    import math
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    rank = math.ceil(p / 100.0 * n)
    rank = max(1, min(rank, n))
    return sorted_vals[rank - 1]


def build_latency_summary(
    samples: list[LatencySample],
    *,
    run_id: str,
    venue: str = "bitfinex_paper",
) -> dict:
    """Build a latency summary dict from a list of LatencySample.

    Accepted real round trips (accepted=True AND shadow_synthetic=False) feed the
    authoritative stats. Rejected/failed acks are excluded from p99 — an unacknowledged
    or error-path order did not complete the venue round-trip and must not skew the
    measured distribution (LATENCY.md §10.3).

    measured is forced False if any shadow_synthetic sample is present (honest gate —
    shadow_synthetic_present is included in the dict so the gate is visible to callers).
    """
    total = len(samples)
    shadow_synthetic_present = any(s.shadow_synthetic for s in samples)

    # Authoritative latencies: real accepted pairs only.
    accepted_real = [s for s in samples if s.accepted and not s.shadow_synthetic]
    latencies_ms = [s.latency_ms for s in accepted_real]
    paired_count = len(latencies_ms)

    rejected_or_unaccepted = total - paired_count

    # n-gate per LATENCY.md §10.3; shadow presence blocks regardless of count.
    measured = paired_count >= MIN_AUTHORITATIVE_SAMPLES and not shadow_synthetic_present

    p99 = percentile(latencies_ms, 99) if latencies_ms else 0.0
    p50 = percentile(latencies_ms, 50) if latencies_ms else 0.0
    min_ms = min(latencies_ms) if latencies_ms else 0.0
    max_ms = max(latencies_ms) if latencies_ms else 0.0

    return {
        "run_id": run_id,
        "venue": venue,
        "first_batch_complete": paired_count >= FIRST_BATCH_TARGET,
        "total_samples": total,
        "rejected_or_unaccepted": rejected_or_unaccepted,
        "order_ack_p50_ms": p50,
        "order_ack_p99_ms": p99,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "shadow_synthetic_present": shadow_synthetic_present,
        "paper_order_latency": {
            "measured": measured,
            "authoritative": measured,
            "paired_count": paired_count,
            "source": "bitfinex_paper",
            "measurement_tier": "rest_sync",
        },
    }


def write_latency_summary(summary: dict, path: Path) -> Path:
    """Write summary as indented JSON with trailing newline; create parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class CryptoPaperHarness:
    """C8 paper harness — drives an adapter, drains LatencySamples, writes summary."""

    def __init__(self, adapter, *, run_id: str, symbol: str = "BTCUSDT") -> None:
        self._adapter = adapter
        self._run_id = run_id
        self._symbol = symbol

    def _make_intent(self, side: str, qty: float, price: float) -> OrderIntent:
        return OrderIntent(
            intent_id=new_intent_id(),
            run_id=self._run_id,
            timestamp_ns=time.time_ns(),  # fresh — risk gate requires it
            strategy_id="c8_paper_harness",
            model_id="c8_paper_harness",
            symbol=self._symbol,
            side=side,
            order_type="LIMIT",
            price=price,
            quantity=qty,
            time_in_force="GTC",
        )

    def run_batch(
        self,
        n: int,
        *,
        base_price: float,
        qty: float = 0.0001,
        alternate_sides: bool = True,
    ) -> list[LatencySample]:
        """Submit n orders and return accumulated LatencySamples.

        Alternates BUY/SELL so the C7 risk position guard does not saturate.
        Real paper-account runs accumulate across sessions toward n>=1000 (C9);
        this harness records one batch — the C9 campaign re-runs until
        paired_count>=1000.
        Pure driver — no sleeps required for the fake; real venue runs naturally
        pace via REST round-trips.
        """
        sides = ["BUY", "SELL"]
        accumulated: list[LatencySample] = []
        for i in range(n):
            side = sides[i % 2] if alternate_sides else "BUY"
            intent = self._make_intent(side, qty, base_price)
            self._adapter.submit_order(intent)
            accumulated.extend(self._adapter.drain_latency_samples())
        return accumulated

    def accumulate_and_write(
        self,
        samples: list[LatencySample],
        *,
        summary_path: Path,
    ) -> dict:
        """Build summary from samples, write to summary_path, return summary dict."""
        summary = build_latency_summary(samples, run_id=self._run_id)
        write_latency_summary(summary, summary_path)
        return summary
