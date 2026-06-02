"""DefensiveModel base class for HFT3 (Phase 7).

Defensive models are siblings to `WorkbenchModel` (alpha/PDF), not
subclasses. The contract is fundamentally different: an alpha model
*generates* a trade signal; a defensive model *modifies* an existing
signal by vetoing, skewing, throttling, or tagging it.

A `DefensiveModel` exposes:

- `model_id`              (str) — registered in `_DEFENSIVE_IDS` or
                          `apps/workbench/config/model_catalog.yaml`
- `phase`                 ("before" | "during" | "after" | "continuous")
- `budget_us`             (float) — max wall time per call
- `defend(ctx)` -> `FilterDecision` — pure: takes (signal, market_ctx)
                          and returns a `FilterDecision` (veto / skew /
                          throttle / tag). The function is *pure* in the
                          sense that it must not mutate `ctx`.
- `validate_inputs(ctx)`  -> List[str] — return list of validation errors
- `produce_diagnostics()` -> Diagnostics

The composition orchestrator
(`apps/workbench/src/registry/composition_orchestrator.py`) wires
defensives via the `DefensiveStub` shim; this class is the **class
boundary** that locks the defensive contract. Code that wants to add a
new defensive model must subclass `DefensiveModel`, not `WorkbenchModel`.

The 26-phase spec requires config-driven testing of these combinations
without manual code changes:

- alpha-only
- defensive-only
- alpha + one defensive
- alpha + multiple defensives
- hybrid (alpha + structural)
- no-defensive baseline
- defensive ablation (one defensive at a time, no-defensive)
- hybrid improvement / degradation detection

The `MODEL_COMBINATIONS` constant enumerates the canonical test matrix.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "FilterDecision",
    "FilterAction",
    "DefensiveDiagnostics",
    "DefensiveModel",
    "MODEL_COMBINATIONS",
]


class FilterAction(str, Enum):
    """What a defensive model can do to a signal."""

    VETO = "veto"          # block the trade entirely
    SKEW = "skew"          # multiply signal strength
    THROTTLE = "throttle"  # rate-limit (e.g. skip this one)
    TAG = "tag"            # attach metadata, do not block


@dataclass(frozen=True)
class FilterDecision:
    """The output of a defensive model's `defend()` call.

    - `action` is the kind of modification applied
    - `vetoed` is True iff the trade should be blocked
    - `skew` is a multiplier on the signal (1.0 = no change)
    - `reason_code` is a stable UPPER_SNAKE_CASE identifier
    - `tags` is a dict of metadata (e.g. {"regime": "high_vol"})
    """

    action: FilterAction
    vetoed: bool = False
    skew: float = 1.0
    reason_code: str = ""
    tags: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passthrough(cls, reason_code: str = "DEFENSIVE_PASSTHROUGH") -> "FilterDecision":
        return cls(action=FilterAction.TAG, reason_code=reason_code)

    @classmethod
    def veto(cls, reason_code: str, tags: Optional[Dict[str, Any]] = None) -> "FilterDecision":
        return cls(
            action=FilterAction.VETO, vetoed=True,
            reason_code=reason_code, tags=tags or {},
        )

    @classmethod
    def skew_signal(
        cls, multiplier: float, reason_code: str, tags: Optional[Dict[str, Any]] = None,
    ) -> "FilterDecision":
        return cls(
            action=FilterAction.SKEW, skew=multiplier,
            reason_code=reason_code, tags=tags or {},
        )

    @classmethod
    def throttle(cls, reason_code: str, tags: Optional[Dict[str, Any]] = None) -> "FilterDecision":
        return cls(
            action=FilterAction.THROTTLE, reason_code=reason_code, tags=tags or {},
        )


@dataclass
class DefensiveDiagnostics:
    model_id: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    series: Dict[str, List[float]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class DefensiveModel(ABC):
    """Sister ABC to `WorkbenchModel` for defensive models.

    Defensive models do not generate signals — they filter, skew, throttle,
    or tag signals produced by alpha models. The composition orchestrator
    runs defensives in their declared `phase` and accumulates their
    `FilterDecision` objects into a `CompositionTrace`.
    """

    model_id: str = ""
    phase: str = "during"  # "before" | "during" | "after" | "continuous"
    budget_us: float = 2500.0
    blocks_trade: bool = True

    @abstractmethod
    def validate_inputs(self, ctx: Any) -> List[str]:
        """Return list of validation errors (empty if OK)."""

    @abstractmethod
    def defend(self, ctx: Any, signal: Any) -> FilterDecision:
        """Pure: do not mutate `ctx` or `signal`. Return a `FilterDecision`."""

    def produce_diagnostics(self, ctx: Any, result: FilterDecision) -> DefensiveDiagnostics:
        """Default: no diagnostics. Subclasses may override."""
        return DefensiveDiagnostics(model_id=self.model_id)


# Canonical test matrix for the 26-phase "no manual code changes" requirement.
# Each entry is a `(name, has_alpha, defensive_ids, structural_ids)` tuple
# that the test suite walks. Adding a new combination is a one-line change
# here — no code edits in the composition orchestrator.
MODEL_COMBINATIONS: List[Dict[str, Any]] = [
    {"name": "alpha_only", "alpha": True, "defensives": [], "structurals": []},
    {"name": "no_defensive_baseline", "alpha": True, "defensives": [], "structurals": []},
    {"name": "alpha_plus_one_defensive", "alpha": True, "defensives": ["regime_filter"], "structurals": []},
    {"name": "alpha_plus_multiple_defensives", "alpha": True, "defensives": ["regime_filter", "throttle", "skew"], "structurals": []},
    {"name": "hybrid_alpha_plus_structural", "alpha": True, "defensives": [], "structurals": ["pdf_topology_1"]},
    {"name": "hybrid_alpha_plus_defensive_plus_structural", "alpha": True, "defensives": ["regime_filter"], "structurals": ["pdf_topology_1"]},
    {"name": "defensive_only", "alpha": False, "defensives": ["regime_filter"], "structurals": []},
    {"name": "ablation_no_defensives", "alpha": True, "defensives": [], "structurals": []},
    {"name": "ablation_regime_filter_only", "alpha": True, "defensives": ["regime_filter"], "structurals": []},
    {"name": "ablation_throttle_only", "alpha": True, "defensives": ["throttle"], "structurals": []},
]
