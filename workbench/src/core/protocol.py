"""WorkbenchModel protocol and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from workbench.src.core.composition import (
    CatalogEntry,
    CompositionTrace,
    CompositionTraceStep,
    DefensiveStub,
    ModelComposition,
    ModelRole,
    Phase,
)

__all__ = [
    "CatalogEntry",
    "CompositionTrace",
    "CompositionTraceStep",
    "DefensiveStub",
    "Diagnostics",
    "ModelComposition",
    "ModelConfig",
    "ModelRole",
    "Phase",
    "WorkbenchModel",
]


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
