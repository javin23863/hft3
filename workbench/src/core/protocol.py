"""WorkbenchModel protocol and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

Phase = Literal["before", "during", "after", "continuous"]
ModelRole = Literal["alpha", "defensive", "hybrid"]


@dataclass(frozen=True)
class CatalogEntry:
    model_id: str
    display_name: str
    description: str
    role: ModelRole = "alpha"
    default_phase: Phase = "during"
    budget_us: float = 2500.0
    blocks_trade: bool = False
    requires: tuple[str, ...] = ()


@dataclass
class DefensiveStub:
    model_id: str
    phase: Phase
    budget_us: float
    enabled: bool = True


@dataclass
class ModelComposition:
    primary_model_id: str
    defensive_stubs: List[DefensiveStub] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_model_id": self.primary_model_id,
            "defensive_stubs": [
                {
                    "model_id": s.model_id,
                    "phase": s.phase,
                    "budget_us": s.budget_us,
                    "enabled": s.enabled,
                }
                for s in self.defensive_stubs
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelComposition":
        stubs = [
            DefensiveStub(
                model_id=str(s["model_id"]),
                phase=s["phase"],
                budget_us=float(s["budget_us"]),
                enabled=bool(s.get("enabled", True)),
            )
            for s in data.get("defensive_stubs", [])
        ]
        return cls(primary_model_id=str(data["primary_model_id"]), defensive_stubs=stubs)


@dataclass
class CompositionTraceStep:
    model_id: str
    phase: Phase
    budget_us: float
    actual_us: float = 0.0
    vetoed_trade: bool = False
    skew_applied: float = 0.0
    output_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompositionTrace:
    primary_model_id: str
    steps: List[CompositionTraceStep] = field(default_factory=list)
    trades_vetoed: int = 0
    signal_raw: float = 0.0
    signal_adjusted: float = 0.0
    phase_budgets_us: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_model_id": self.primary_model_id,
            "trades_vetoed": self.trades_vetoed,
            "signal_raw": self.signal_raw,
            "signal_adjusted": self.signal_adjusted,
            "phase_budgets_us": self.phase_budgets_us,
            "steps": [
                {
                    "model_id": s.model_id,
                    "phase": s.phase,
                    "budget_us": s.budget_us,
                    "actual_us": s.actual_us,
                    "vetoed_trade": s.vetoed_trade,
                    "skew_applied": s.skew_applied,
                    "output_summary": s.output_summary,
                }
                for s in self.steps
            ],
        }


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    kind: str  # hypothesis | pdf
    name: str = ""
    required_datasets: List[str] = field(default_factory=lambda: ["mbo_npz"])
    min_history_years: int = 10
    robustness_window: str = "discovery"
    latency_lane: str = "sub_10ms"
    execution_assumptions: str = "limit_queue"
    parameter_bounds: Dict[str, List[float]] = field(default_factory=dict)
    signal_field: str = ""
    diagnostics_only: bool = False
    hyp_id: Optional[int] = None


@dataclass
class Diagnostics:
    model_id: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    series: Dict[str, List[float]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class WorkbenchModel(ABC):
    """Unified plugin interface for HYP and PDF models."""

    model_id: str = ""
    config: ModelConfig | None = None

    @abstractmethod
    def validate_inputs(self, ctx: Any) -> List[str]:
        """Return list of validation errors (empty if OK)."""

    @abstractmethod
    def build_features(self, ctx: Any) -> Any:
        ...

    @abstractmethod
    def generate_signals(self, features: Any) -> float:
        ...

    @abstractmethod
    def run_backtest(self, ctx: Any) -> Any:
        ...

    @abstractmethod
    def produce_diagnostics(self, ctx: Any, result: Any) -> Diagnostics:
        ...
