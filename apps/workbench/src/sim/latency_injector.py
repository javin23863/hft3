"""Inject measured C++ latency into backtest timing — never Python wall clock."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from workbench.src.sim.cpp_latency_profile import CppLatencyProfile, LATENCY_INJECTION_SWEEP_US


@dataclass
class PythonResearchTiming:
    """Informational only — NOT used for viability or promotion gates."""

    decision_compute_us: float
    note: str = "Python research runtime; not production latency"


@dataclass
class InjectedDecisionLatency:
    """One decision's latency budget from measured C++ / colo distributions."""

    feed_delay_us: float
    decision_compute_us: float
    decision_to_send_us: float
    send_to_ack_us: float
    total_us: float
    injection_extra_us: float = 0.0
    source: str = "cpp_measured"

    @classmethod
    def from_profile(
        cls,
        profile: CppLatencyProfile,
        rng: random.Random,
        injection_us: float = 0.0,
        percentile: str = "p99",
    ) -> "InjectedDecisionLatency":
        parts = profile.sample_total_us(rng, percentile=percentile)
        extra = max(0.0, injection_us)
        total = parts["total_us"] + extra
        return cls(
            feed_delay_us=parts["feed_delay_us"],
            decision_compute_us=parts["decision_compute_us"],
            decision_to_send_us=parts["decision_to_send_us"],
            send_to_ack_us=parts["send_to_ack_us"],
            total_us=total,
            injection_extra_us=extra,
        )

    def total_ms_for_backtest(self) -> float:
        """Convert injected latency to ms for replay backtest deferral."""
        return self.total_us / 1000.0


class CppLatencyInjector:
    """Apply C++-authoritative latency to backtest and audit paths."""

    def __init__(self, profile: CppLatencyProfile, seed: int = 42):
        self.profile = profile
        self.rng = random.Random(seed)

    def measure_python_research_time(self, fn, *args, **kwargs) -> tuple[Any, PythonResearchTiming]:
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_us = (time.perf_counter() - t0) * 1e6
        return result, PythonResearchTiming(decision_compute_us=elapsed_us)

    def sample_decision_latency(
        self,
        injection_us: float = 0.0,
        percentile: str = "p99",
    ) -> InjectedDecisionLatency:
        return InjectedDecisionLatency.from_profile(
            self.profile, self.rng, injection_us=injection_us, percentile=percentile
        )

    def injection_sweep_us(self) -> List[int]:
        return list(self.profile.injection_sweep_us or LATENCY_INJECTION_SWEEP_US)

    def pnl_decay_heuristic(self, base_pnl: float, injection_us: float) -> float:
        """MVP edge decay vs injected latency (full sweep re-runs backtest with --full-sweep)."""
        prod = self.profile.measured_production_p99_us
        if prod <= 0:
            return base_pnl
        scale = max(0.0, 1.0 - (injection_us / (prod * 3.0)))
        return base_pnl * scale
