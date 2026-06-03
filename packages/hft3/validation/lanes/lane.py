"""Core lane abstractions: Lane enum, WindowConfig, HorizonConfig, LaneConfig Protocol, BacktestResult Protocol.

Lane-aware backtester validation. The existing CME-only certification is
preserved for backward compatibility; this module adds the lane-agnostic
abstractions that adapters use to wrap each lane's native config.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Lane(str, Enum):
    """Asset class / execution lane."""

    CME_FUTURES = "cme_futures"
    CRYPTO = "crypto"
    EQUITIES = "equities"
    OPTIONS = "options"

    @classmethod
    def from_model_id(cls, model_id: str) -> "Lane":
        """Resolve a Lane from a model_id prefix.

        Recognized prefixes:
          - CRYPTO_                    -> CRYPTO
          - EQUITY_, LOW_FLOAT_         -> EQUITIES
          - OPTIONS_, PARITY_           -> OPTIONS
          - everything else             -> CME_FUTURES
        """
        upper = (model_id or "").upper()
        if upper.startswith("CRYPTO_"):
            return cls.CRYPTO
        if upper.startswith(("EQUITY_", "LOW_FLOAT_")):
            return cls.EQUITIES
        if upper.startswith(("OPTIONS_", "PARITY_")):
            return cls.OPTIONS
        return cls.CME_FUTURES


@dataclass(frozen=True)
class LaneCapabilityProfile:
    """Execution capability profile for a lane.

    This separates what a lane can prove operationally from the asset class
    identity. Equities can be fast without being true DMA HFT; crypto can be
    node-direct HFT without being futures/equities DMA.
    """

    name: str
    is_hft: bool
    dma: bool
    node_direct: bool
    speed_advantage: bool
    research_only: bool
    hft_proof_required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CME_TRUE_HFT_DMA_PROFILE = LaneCapabilityProfile(
    name="true_hft_dma",
    is_hft=True,
    dma=True,
    node_direct=False,
    speed_advantage=True,
    research_only=False,
    hft_proof_required=True,
    description="CME futures lane: true HFT with direct market access requirements.",
)

CRYPTO_NODE_DIRECT_HFT_PROFILE = LaneCapabilityProfile(
    name="node_direct_hft",
    is_hft=True,
    dma=False,
    node_direct=True,
    speed_advantage=True,
    research_only=False,
    hft_proof_required=True,
    description="Crypto lane: node-direct HFT for instruments covered by validated crypto data environment.",
)

EQUITIES_SPEED_ADVANTAGE_PROFILE = LaneCapabilityProfile(
    name="speed_advantage_non_dma",
    is_hft=False,
    dma=False,
    node_direct=False,
    speed_advantage=True,
    research_only=False,
    hft_proof_required=False,
    description="Equities lane: speed-advantage research/execution without true DMA HFT requirement.",
)

OPTIONS_RESEARCH_PROFILE = LaneCapabilityProfile(
    name="research_non_hft",
    is_hft=False,
    dma=False,
    node_direct=False,
    speed_advantage=False,
    research_only=True,
    hft_proof_required=False,
    description="Options lane: research/non-HFT unless separately proven.",
)


@dataclass(frozen=True)
class WindowConfig:
    """Time window around an event or for a backtest run.

    All fields are optional. For CME macro events: start_offset/end_offset
    in seconds relative to event anchor. For walk-forward: training_window,
    test_window, embargo in days or seconds. For equities: trading days.
    """

    start_offset_seconds: float | None = None
    end_offset_seconds: float | None = None
    training_window_days: int | None = None
    test_window_days: int | None = None
    embargo_seconds: float | None = None
    label_horizon_ms: int | None = None


@dataclass(frozen=True)
class HorizonConfig:
    """Prediction horizons and lookback windows.

    horizons is a list of either int (days for equities, ms for crypto)
    or str (e.g. "30s", "5m", "1h", "8h" for crypto).
    """

    horizons: list[int | str] = field(default_factory=list)
    lookback_days: int | None = None
    feature_window_days: int | None = None


@runtime_checkable
class LaneConfig(Protocol):
    """Structural type that every lane adapter must expose.

    Each lane implements this implicitly by providing the attributes below.
    No inheritance required.
    """

    lane: Lane
    symbols: list[str]
    windows: WindowConfig
    horizons: HorizonConfig
    latency_bands_ms: list[float]
    tick_size: float
    lot_size: float
    test_paths: list[str]
    event_types: list[str]
    capability_profile: LaneCapabilityProfile

    def to_dict(self) -> dict[str, Any]:
        ...


@runtime_checkable
class BacktestResult(Protocol):
    """Structural type for any backtest result, regardless of lane."""

    lane: Lane
    net_pnl: float
    num_trades: int
    max_drawdown: float
    turnover: float
    degraded: bool
    failure_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        ...


@dataclass
class GenericBacktestResult:
    """Default BacktestResult implementation that any lane adapter can use."""

    lane: Lane
    net_pnl: float = 0.0
    num_trades: int = 0
    max_drawdown: float = 0.0
    turnover: float = 0.0
    degraded: bool = False
    failure_notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "lane": self.lane.value,
            "net_pnl": self.net_pnl,
            "num_trades": self.num_trades,
            "max_drawdown": self.max_drawdown,
            "turnover": self.turnover,
            "degraded": self.degraded,
            "failure_notes": list(self.failure_notes),
        }
        d.update(self.extra)
        return d
