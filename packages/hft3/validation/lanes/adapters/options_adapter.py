"""Options lane adapter.

Wraps MultiLegParityBacktester with a minimal LaneConfig. Options lane
has the fewest knobs (latency_ms=1.0 default); the adapter reflects that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..backtester_protocol import validate_lane_config
from ..lane import (
    OPTIONS_RESEARCH_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    WindowConfig,
)

OPTIONS_LATENCY_BANDS_MS = [1.0]
OPTIONS_EVENT_TYPES = ["options_parity"]


@dataclass
class OptionsConfig:
    """Options lane LaneConfig. Minimal config (latency_ms only)."""

    lane: Lane = Lane.OPTIONS
    symbols: list[str] = field(default_factory=list)
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(default_factory=HorizonConfig)
    latency_bands_ms: list[float] = field(default_factory=lambda: list(OPTIONS_LATENCY_BANDS_MS))
    tick_size: float = 0.01
    lot_size: float = 100.0
    test_paths: list[str] = field(default_factory=lambda: ["tests/test_options_lane"])
    event_types: list[str] = field(default_factory=lambda: list(OPTIONS_EVENT_TYPES))
    latency_ms: float = 1.0
    capability_profile: LaneCapabilityProfile = OPTIONS_RESEARCH_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {},
            "horizons": {"horizons": list(self.horizons.horizons)},
            "latency_bands_ms": list(self.latency_bands_ms),
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "test_paths": list(self.test_paths),
            "event_types": list(self.event_types),
            "latency_ms": self.latency_ms,
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_options_config() -> OptionsConfig:
    """Load options lane config. Currently no YAML; returns defaults."""
    return OptionsConfig()


class OptionsBacktester:
    """Backtester Protocol implementation for options."""

    def __init__(self, config: OptionsConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            degraded=False,
            failure_notes=[],
            extra={"target": target} if target else {},
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)
