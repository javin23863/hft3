"""CME options lane adapter (Phases 0-1: research-only).

Wraps the CME options lane config to satisfy the Backtester Protocol.
Options execution is Phase 2; this adapter is research_only until that gate.

Capability profile: is_hft=False, dma=True (futures DMA on CHI404 exists;
options execution is Phase 2), research_only=True FOR NOW.
The profile flips to research_only=False at the Phase 2 gate — do not claim
execution edge while research_only is True.

Model-id prefix: FOPT_ (canonical CME options prefix).
OPTIONS_ is a legacy equities-parity prefix; FOPT_ is the new standard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..backtester_protocol import validate_lane_config
from ..lane import (
    CME_OPTIONS_RESEARCH_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    WindowConfig,
)

# CME options underliers: equity index options on futures (ES, NQ, YM, RTY)
# and select commodity options (CL, GC). Tick sizes vary; defaults here are
# ES options at 0.05 index points. Override per-symbol at Phase 2.
CME_OPTIONS_SYMBOLS = ["ESO", "NQO", "YMO", "RTYO", "CLO", "GCO"]
CME_OPTIONS_LATENCY_BANDS_MS = [1.0, 2.0, 5.0, 10.0]
CME_OPTIONS_EVENT_TYPES = ["macro", "synthetic", "options_expiry", "options_roll"]


@dataclass
class CMEOptionsConfig:
    """CME options lane LaneConfig.

    research_only=True while execution is in Phases 0-1.
    At Phase 2 gate, update capability_profile to a non-research profile.
    """

    lane: Lane = Lane.CME_OPTIONS
    symbols: list[str] = field(default_factory=lambda: list(CME_OPTIONS_SYMBOLS))
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(default_factory=HorizonConfig)
    latency_bands_ms: list[float] = field(default_factory=lambda: list(CME_OPTIONS_LATENCY_BANDS_MS))
    tick_size: float = 0.05
    lot_size: float = 1.0
    test_paths: list[str] = field(
        default_factory=lambda: ["tests/test_lanes_cme_options"]
    )
    event_types: list[str] = field(default_factory=lambda: list(CME_OPTIONS_EVENT_TYPES))
    capability_profile: LaneCapabilityProfile = CME_OPTIONS_RESEARCH_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {
                "start_offset_seconds": self.windows.start_offset_seconds,
                "end_offset_seconds": self.windows.end_offset_seconds,
            },
            "horizons": {
                "horizons": list(self.horizons.horizons),
                "lookback_days": self.horizons.lookback_days,
            },
            "latency_bands_ms": list(self.latency_bands_ms),
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "test_paths": list(self.test_paths),
            "event_types": list(self.event_types),
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_cme_options_config() -> CMEOptionsConfig:
    """Load CME options lane config. Returns defaults (no external config file yet)."""
    return CMEOptionsConfig()


class CMEOptionsBacktester:
    """Backtester Protocol implementation for CME options.

    research_only=True for Phases 0-1. run() returns a structural result;
    no real backtest execution occurs (options execution is Phase 2).
    """

    def __init__(self, config: CMEOptionsConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            net_pnl=0.0,
            num_trades=0,
            max_drawdown=0.0,
            turnover=0.0,
            degraded=False,
            failure_notes=[],
            extra={"target": target, "research_only": self.config.capability_profile.research_only},
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)
