"""Lane-aware backtester validation (Option C: full unification)."""

from .backtester_protocol import Backtester, validate_lane_config
from .lane import (
    BacktestResult,
    CME_OPTIONS_RESEARCH_PROFILE,
    CME_TRUE_HFT_DMA_PROFILE,
    EQUITIES_SPEED_ADVANTAGE_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    WindowConfig,
)
from .lane_registry import LaneRegistration, LaneRegistry, register_lane
from .registration import register_all_lanes

__all__ = [
    "Backtester",
    "BacktestResult",
    "CME_OPTIONS_RESEARCH_PROFILE",
    "CME_TRUE_HFT_DMA_PROFILE",
    "EQUITIES_SPEED_ADVANTAGE_PROFILE",
    "GenericBacktestResult",
    "HorizonConfig",
    "Lane",
    "LaneCapabilityProfile",
    "LaneConfig",
    "LaneRegistration",
    "LaneRegistry",
    "WindowConfig",
    "register_all_lanes",
    "register_lane",
    "validate_lane_config",
]
