"""Runtime temporal leakage auditor for MBOFeatureExtractor — verifies F_t filtration integrity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np

from features_engine.src.features.feature_index import FEATURE_DIM
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor


@dataclass
class PerturbationResult:
    perturb_idx: int
    leak_dimensions: List[int]
    leak_offsets: List[int]


@dataclass
class TemporalLeakageReport:
    passed: bool
    total_events: int
    perturbations_run: int
    perturbations_failed: int
    leak_details: List[PerturbationResult]
    window_reset_test_passed: bool
    error_message: str = ""


class TemporalLeakageChecker:
    """Instruments MBOFeatureExtractor and verifies no feature leaks future information."""

    def __init__(self, tick_size: float = 0.25, rolling_window_ns: int = 1_000_000_000):
        self.tick_size = tick_size
        self.rolling_window_ns = rolling_window_ns

    def run_audit(self, events: List[MBOEvent]) -> TemporalLeakageReport:
        if len(events) < 10:
            return TemporalLeakageReport(
                passed=False,
                total_events=len(events),
                perturbations_run=0,
                perturbations_failed=0,
                leak_details=[],
                window_reset_test_passed=False,
                error_message="Need at least 10 events for audit",
            )

        base_vectors = self._feed_extractor(events)

        perturbation_results: List[PerturbationResult] = []
        for k in range(5, len(events) - 5, 5):
            result = self._perturbation_test(events, base_vectors, k)
            perturbation_results.append(result)

        perturbations_failed = sum(1 for r in perturbation_results if r.leak_dimensions)
        window_pass = self._window_reset_test(events)
        passed = perturbations_failed == 0 and window_pass

        return TemporalLeakageReport(
            passed=passed,
            total_events=len(events),
            perturbations_run=len(perturbation_results),
            perturbations_failed=perturbations_failed,
            leak_details=perturbation_results,
            window_reset_test_passed=window_pass,
        )

    def _feed_extractor(self, events: List[MBOEvent]) -> List[np.ndarray]:
        extractor = MBOFeatureExtractor(
            tick_size=self.tick_size, rolling_window_ns=self.rolling_window_ns
        )
        vectors: List[np.ndarray] = []
        for event in events:
            vec = extractor.process_event(event)
            vectors.append(vec.copy())
        return vectors

    def _perturbation_test(
        self,
        base_events: List[MBOEvent],
        base_vectors: List[np.ndarray],
        perturb_idx: int,
    ) -> PerturbationResult:
        ev = base_events[perturb_idx]
        use_price_perturb = (
            ev.action == "ADD"
            and len(base_events) >= perturb_idx + 2
        )
        if use_price_perturb:
            new_price = ev.price + self.tick_size * (1 if ev.side == "A" else -1)
            new_price = max(new_price, self.tick_size)
            perturbed_event = MBOEvent(
                timestamp_ns=ev.timestamp_ns,
                order_id=ev.order_id,
                action=ev.action,
                side=ev.side,
                price=new_price,
                size=ev.size,
            )
        else:
            perturbed_event = MBOEvent(
                timestamp_ns=ev.timestamp_ns,
                order_id=ev.order_id,
                action=ev.action,
                side=ev.side,
                price=ev.price,
                size=ev.size * 2 + 1,
            )

        extractor_b = MBOFeatureExtractor(
            tick_size=self.tick_size, rolling_window_ns=self.rolling_window_ns
        )
        perturbed_vectors: List[np.ndarray] = []
        for i in range(perturb_idx + 1):
            if i == perturb_idx:
                vec = extractor_b.process_event(perturbed_event)
            else:
                vec = extractor_b.process_event(base_events[i])
            perturbed_vectors.append(vec.copy())

        leak_dimensions: List[int] = []
        leak_offsets: List[int] = []

        for i in range(perturb_idx):
            a = base_vectors[i]
            b = perturbed_vectors[i]
            for d in range(FEATURE_DIM):
                if not (abs(float(a[d]) - float(b[d])) < 1e-9):
                    leak_dimensions.append(d)
                    leak_offsets.append(perturb_idx - i)

        return PerturbationResult(
            perturb_idx=perturb_idx,
            leak_dimensions=leak_dimensions,
            leak_offsets=leak_offsets,
        )

    def _window_reset_test(self, events: List[MBOEvent]) -> bool:
        extractor = MBOFeatureExtractor(
            tick_size=self.tick_size, rolling_window_ns=self.rolling_window_ns
        )

        half = len(events) // 2
        for i in range(half):
            extractor.process_event(events[i])

        gap_event = MBOEvent(
            timestamp_ns=events[half - 1].timestamp_ns + self.rolling_window_ns * 2,
            order_id=9999000,
            action="ADD",
            side="B",
            price=100.00,
            size=10,
        )
        extractor.process_event(gap_event)

        book_valid = extractor.book.get_best_bid() >= 0.0
        agg_reset = extractor.buy_agg_vol == 0 and extractor.sell_agg_vol == 0
        accumulators_reset = (
            extractor.add_vol == 0
            and extractor.cancel_vol == 0
            and extractor.bid_add_vol == 0
            and extractor.ask_add_vol == 0
            and extractor.bid_cancel_vol == 0
            and extractor.ask_cancel_vol == 0
            and extractor.near_touch_cancel_vol == 0
        )

        return book_valid and agg_reset and accumulators_reset


def generate_test_events(num_events: int = 40, seed: int = 42) -> List[MBOEvent]:
    """Generate a deterministic sequence of synthetic MBO events that exercises
    all branches: ADD, CANCEL, TRADE, MODIFY at various price levels.
    Returns events where timestamps are monotonically increasing by ~10us steps."""
    _rng = np.random.default_rng(seed)
    base_ts = 1_700_000_000_000_000_000
    step_ns = 10_000
    events: List[MBOEvent] = []
    oid = 300

    def _ts():
        return base_ts + len(events) * step_ns

    bid_prices = [101.00, 100.75, 100.50, 100.25, 100.00]
    ask_prices = [101.25, 101.50, 101.75, 102.00, 102.25]
    bid_oids = list(range(300, 305))
    ask_oids = list(range(305, 310))

    for p in bid_prices:
        events.append(MBOEvent(_ts(), oid, "ADD", "B", p, 10))
        oid += 1
    for p in ask_prices:
        events.append(MBOEvent(_ts(), oid, "ADD", "A", p, 10))
        oid += 1

    for o, p in zip(bid_oids, bid_prices):
        events.append(MBOEvent(_ts(), o, "TRADE", "B", p, 3))

    for o, p in zip(ask_oids[:3], ask_prices[:3]):
        events.append(MBOEvent(_ts(), o, "TRADE", "A", p, 3))

    events.append(MBOEvent(_ts(), bid_oids[4], "CANCEL", "B", bid_prices[4], 7))
    events.append(MBOEvent(_ts(), ask_oids[4], "CANCEL", "A", ask_prices[4], 7))
    events.append(MBOEvent(_ts(), bid_oids[2], "MODIFY", "B", bid_prices[2], 20))

    while len(events) < num_events:
        choice = len(events) % 3
        side_cycle = "B" if (len(events) // 3) % 2 == 0 else "A"
        p = round(100.0 + 0.25 * (len(events) % 10 - 5), 3)
        if p <= 0:
            p = 100.0
        if choice == 0:
            events.append(MBOEvent(_ts(), oid, "ADD", side_cycle, p, 5))
            oid += 1
        elif choice == 1:
            target_oid = oid - 1 if oid > 300 else 300
            events.append(MBOEvent(_ts(), target_oid, "CANCEL", side_cycle, p, 5))
        else:
            target_oid = oid - 1 if oid > 300 else 300
            events.append(MBOEvent(_ts(), target_oid, "TRADE", side_cycle, p, 2))

    return events


def run_temporal_audit(tick_size: float = 0.25) -> TemporalLeakageReport:
    """Quick audit with synthetic events. Returns report."""
    events = generate_test_events(num_events=40)
    checker = TemporalLeakageChecker(tick_size=tick_size)
    return checker.run_audit(events)
