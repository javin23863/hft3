"""Eight-mode imbalance ablation matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional


class ImbalanceFamily(str, Enum):
    BOOK = "book_imbalance"
    ORDER_FLOW = "order_flow"
    AUCTION = "auction_imbalance"


@dataclass(frozen=True)
class ImbalanceAblationMode:
    mode_id: str
    active_families: frozenset[ImbalanceFamily]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "active_families": [f.value for f in self.active_families],
            "description": self.description,
        }


def all_ablation_modes() -> List[ImbalanceAblationMode]:
    b, o, a = ImbalanceFamily.BOOK, ImbalanceFamily.ORDER_FLOW, ImbalanceFamily.AUCTION
    return [
        ImbalanceAblationMode("baseline", frozenset(), "No imbalance families"),
        ImbalanceAblationMode("book_only", frozenset({b}), "Book imbalance only"),
        ImbalanceAblationMode("order_flow_only", frozenset({o}), "Order-flow only"),
        ImbalanceAblationMode("auction_only", frozenset({a}), "Auction imbalance only"),
        ImbalanceAblationMode("book_order_flow", frozenset({b, o}), "Book + order-flow"),
        ImbalanceAblationMode("book_auction", frozenset({b, a}), "Book + auction"),
        ImbalanceAblationMode("order_flow_auction", frozenset({o, a}), "Order-flow + auction"),
        ImbalanceAblationMode("all_three", frozenset({b, o, a}), "All imbalance families"),
    ]


def iter_ablation_modes() -> Iterator[ImbalanceAblationMode]:
    yield from all_ablation_modes()


def family_enabled(mode: ImbalanceAblationMode, family: ImbalanceFamily) -> bool:
    return family in mode.active_families


@dataclass
class AblationRunResult:
    mode_id: str
    baseline_metric: float
    treatment_metric: float
    incremental_contribution: float
    decision: str
    latency_cost_ns: int = 0
    storage_cost_bytes: int = 0
    robustness_passed: bool = True
    walk_forward_passed: bool = True
    walk_forward_correlation: float = 0.0
    labeling: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode_id": self.mode_id,
            "baseline_metric": self.baseline_metric,
            "treatment_metric": self.treatment_metric,
            "incremental_contribution": self.incremental_contribution,
            "decision": self.decision,
            "latency_cost_ns": self.latency_cost_ns,
            "storage_cost_bytes": self.storage_cost_bytes,
            "robustness_passed": self.robustness_passed,
            "walk_forward_passed": self.walk_forward_passed,
            "walk_forward_correlation": self.walk_forward_correlation,
            "labeling": self.labeling or {},
        }


def decide_promotion(
    incremental: float,
    *,
    latency_ok: bool = True,
    robustness_ok: bool = True,
    wfc_ok: bool = True,
) -> str:
    if not latency_ok:
        return "quarantine"
    if not robustness_ok or not wfc_ok:
        return "quarantine"
    if incremental > 0:
        return "promote"
    if incremental < 0:
        return "reject"
    return "quarantine"


def best_ablation_verdict(results: List[AblationRunResult]) -> tuple[str, Optional[str]]:
    """Return (verdict, best_mode_id) using max incremental vs baseline."""
    baseline = next((r for r in results if r.mode_id == "baseline"), None)
    if baseline is None:
        return "quarantine", None
    candidates = [r for r in results if r.mode_id != "baseline"]
    if not candidates:
        return "quarantine", None
    best = max(candidates, key=lambda r: r.incremental_contribution)
    if best.incremental_contribution > 0 and best.decision == "promote":
        return "promote", best.mode_id
    if best.incremental_contribution < 0:
        return "reject", best.mode_id
    return "quarantine", best.mode_id
