"""Register all lane adapters with the LaneRegistry.

Importing this module populates the LaneRegistry singleton with all
lanes. The unified certification runner and promotion gate depend on
this registration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .adapters.cme_adapter import CMEBacktester, CMEConfig, load_cme_config
from .adapters.cme_options_adapter import (
    CMEOptionsBacktester,
    load_cme_options_config,
)
from .backtester_protocol import validate_lane_config
from .lane import (
    EQUITIES_SPEED_ADVANTAGE_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    WindowConfig,
)
from .lane_registry import LaneRegistry, register_lane

# Lane.EQUITIES is the historical name of the options/parity lane
# (CME futures options via packages/options_lane). The dedicated
# equities/crypto lane adapters moved out with their lanes; the
# registration is kept here with a minimal structural config/backtester
# (same shim pattern as CMEBacktester: real validation happens via
# pytest on test_paths) so promotion-gate coverage for OPTIONS_/PARITY_
# models keeps working.

OPTIONS_LANE_SYMBOLS = ["OPTIONS", "PARITY"]
# Bands are reporting/sweep documentation only for this lane; gating is floor-based.
OPTIONS_LANE_LATENCY_BANDS_MS = [5.0, 10.0, 50.0, 100.0, 250.0]
OPTIONS_LANE_EVENT_TYPES = ["options_parity", "parity"]


@dataclass
class OptionsLaneConfig:
    """Options/parity lane LaneConfig (registered under Lane.EQUITIES)."""

    lane: Lane = Lane.EQUITIES
    symbols: list[str] = field(default_factory=lambda: list(OPTIONS_LANE_SYMBOLS))
    windows: WindowConfig = field(default_factory=WindowConfig)
    horizons: HorizonConfig = field(default_factory=HorizonConfig)
    latency_bands_ms: list[float] = field(default_factory=lambda: list(OPTIONS_LANE_LATENCY_BANDS_MS))
    tick_size: float = 0.25
    lot_size: float = 1.0
    test_paths: list[str] = field(
        default_factory=lambda: ["tests/test_workbench/test_options_lane_campaign.py"]
    )
    event_types: list[str] = field(default_factory=lambda: list(OPTIONS_LANE_EVENT_TYPES))
    latency_floor_ms: float = 5.0
    capability_profile: LaneCapabilityProfile = EQUITIES_SPEED_ADVANTAGE_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "symbols": list(self.symbols),
            "windows": {
                "training_window_days": self.windows.training_window_days,
                "test_window_days": self.windows.test_window_days,
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
            "latency_floor_ms": self.latency_floor_ms,
            "capability_profile": self.capability_profile.to_dict(),
        }


def load_options_lane_config() -> OptionsLaneConfig:
    """Load the options/parity lane config (static; no external file)."""
    return OptionsLaneConfig()


class OptionsLaneBacktester:
    """Backtester Protocol implementation for the options/parity lane.

    run() does not execute a real backtest (the real options-lane
    validation happens in tests/test_workbench/test_options_lane_campaign.py
    and packages/options_lane); it returns a structural result so the
    unified certification runner can verify config and coverage.
    """

    def __init__(self, config: OptionsLaneConfig) -> None:
        self.config = config

    def run(self, target: str | None = None) -> GenericBacktestResult:
        return GenericBacktestResult(
            lane=self.config.lane,
            net_pnl=0.0,
            num_trades=0,
            max_drawdown=0.0,
            turnover=0.0,
            degraded=True,
            failure_notes=["structural-only options/parity adapter; no evidence backtest executed"],
            extra={
                **({"target": target} if target else {}),
                "structural_only": True,
                "promotable": False,
            },
        )

    def validate_config(self) -> list[str]:
        return validate_lane_config(self.config)


def _cme_validator() -> "CMEBacktester":
    return CMEBacktester(load_cme_config())


def _options_lane_validator() -> "OptionsLaneBacktester":
    return OptionsLaneBacktester(load_options_lane_config())


def _cme_options_validator() -> "CMEOptionsBacktester":
    return CMEOptionsBacktester(load_cme_options_config())


def register_all_lanes() -> None:
    """Register all lanes. Idempotent: safe to call multiple times."""
    reg = LaneRegistry.instance()
    if reg.get(Lane.CME_FUTURES) is None:
        register_lane(
            lane=Lane.CME_FUTURES,
            adapter_factory=lambda: CMEBacktester(load_cme_config()),
            config_loader=load_cme_config,
            validator=_cme_validator,
            test_paths=["tests/backtester_validation/fast", "tests/backtester_validation/full"],
            model_id_prefixes=(),
        )
    if reg.get(Lane.EQUITIES) is None:
        register_lane(
            lane=Lane.EQUITIES,
            adapter_factory=lambda: OptionsLaneBacktester(load_options_lane_config()),
            config_loader=load_options_lane_config,
            validator=_options_lane_validator,
            test_paths=["tests/test_workbench/test_options_lane_campaign.py"],
            model_id_prefixes=("OPTIONS_", "PARITY_"),
        )
    if reg.get(Lane.CME_OPTIONS) is None:
        register_lane(
            lane=Lane.CME_OPTIONS,
            adapter_factory=lambda: CMEOptionsBacktester(load_cme_options_config()),
            config_loader=load_cme_options_config,
            validator=_cme_options_validator,
            test_paths=["tests/test_lanes_cme_options"],
            model_id_prefixes=("FOPT_",),
        )


# Auto-register on import so consumers can use the registry without an explicit call.
register_all_lanes()
