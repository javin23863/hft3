"""Defensive composition types (catalog + campaign stubs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal

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
