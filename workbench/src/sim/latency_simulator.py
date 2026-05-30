"""Latency simulation — delegates to measured C++ profile (not Python wall time)."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from workbench.src.sim.cpp_latency_profile import CppLatencyProfile, LATENCY_INJECTION_SWEEP_US


@dataclass
class LatencyPolicy:
    """Backtest latency policy backed by C++ measured distributions."""

    mode: str = "cpp_measured"  # cpp_measured | injection_sweep
    cpp_profile: Optional[CppLatencyProfile] = None
    injection_us: float = 0.0
    percentile: str = "p99"
    seed: int = 42

    @property
    def mean_ms(self) -> float:
        if self.cpp_profile:
            total = self.cpp_profile.measured_production_p99_us + self.injection_us
            return total / 1000.0
        return 1.0

    @classmethod
    def fixed(cls, latency_ms: float) -> "LatencyPolicy":
        prof = CppLatencyProfile.from_yaml_defaults()
        return cls(mode="cpp_measured", cpp_profile=prof, injection_us=max(0, latency_ms * 1000 - prof.measured_production_p99_us))

    @classmethod
    def from_chi404_summary(cls, summary_path: Path, seed: int = 42) -> "LatencyPolicy":
        return cls(
            mode="cpp_measured",
            cpp_profile=CppLatencyProfile.from_chi404_summary(summary_path),
            seed=seed,
        )

    @classmethod
    def from_cpp_profile(cls, profile: CppLatencyProfile, injection_us: float = 0.0) -> "LatencyPolicy":
        return cls(mode="cpp_measured", cpp_profile=profile, injection_us=injection_us)

    def total_ms_for_backtest(self, rng: Optional[random.Random] = None) -> float:
        rng = rng or random.Random(self.seed)
        if not self.cpp_profile:
            return 1.0
        from workbench.src.sim.latency_injector import InjectedDecisionLatency

        inj = InjectedDecisionLatency.from_profile(
            self.cpp_profile, rng, injection_us=self.injection_us, percentile=self.percentile
        )
        return inj.total_ms_for_backtest()

    def injection_sweep_us(self) -> List[int]:
        if self.cpp_profile:
            return self.cpp_profile.injection_sweep_us
        return list(LATENCY_INJECTION_SWEEP_US)
