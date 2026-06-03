"""Lane-aware backtester validation (Option C: full unification)."""

from .backtester_protocol import Backtester, validate_lane_config
from .lane import (
    BacktestResult,
    CME_TRUE_HFT_DMA_PROFILE,
    CRYPTO_NODE_DIRECT_HFT_PROFILE,
    EQUITIES_SPEED_ADVANTAGE_PROFILE,
    GenericBacktestResult,
    HorizonConfig,
    Lane,
    LaneCapabilityProfile,
    LaneConfig,
    OPTIONS_RESEARCH_PROFILE,
    WindowConfig,
)
from .lane_registry import LaneRegistration, LaneRegistry, register_lane
from .registration import register_all_lanes

__all__ = [
    "Backtester",
    "BacktestResult",
    "CME_TRUE_HFT_DMA_PROFILE",
    "CRYPTO_NODE_DIRECT_HFT_PROFILE",
    "EQUITIES_SPEED_ADVANTAGE_PROFILE",
    "GenericBacktestResult",
    "HorizonConfig",
    "Lane",
    "LaneCapabilityProfile",
    "LaneConfig",
    "LaneRegistration",
    "LaneRegistry",
    "OPTIONS_RESEARCH_PROFILE",
    "WindowConfig",
    "register_all_lanes",
    "register_lane",
    "validate_lane_config",
]
