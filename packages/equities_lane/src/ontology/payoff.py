"""StockOptionPayoffComparison and StockOptionRouteDecision ontology objects."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .citations import default_citations_for_route, require_grounding

ROUTE_STOCK_ONLY = "STOCK_ONLY"
ROUTE_OPTION_ONLY = "OPTION_ONLY"
ROUTE_STOCK_AND_OPTION = "STOCK_AND_OPTION"
ROUTE_NO_TRADE = "NO_TRADE"

VALID_ROUTES = frozenset(
    {ROUTE_STOCK_ONLY, ROUTE_OPTION_ONLY, ROUTE_STOCK_AND_OPTION, ROUTE_NO_TRADE}
)


@dataclass(frozen=True)
class StockOptionPayoffComparison:
    underlying_symbol: str
    session_date: str
    decision_timestamp_ns: int
    stock_expected_value: float
    option_expected_value: float
    stock_plus_option_expected_value: float
    expected_slippage_stock: float
    expected_slippage_option: float
    spread_cost_stock: float
    spread_cost_option: float
    fill_probability_stock: float
    fill_probability_option: float
    latency_assumption_stock_us: float
    latency_assumption_option_us: float
    max_loss_stock: float
    max_loss_option: float
    convexity_exposure: float
    gamma_exposure: float
    delta_exposure: float
    theta_decay_window_seconds: float
    liquidity_score_stock: float
    liquidity_score_option: float
    borrow_shortability_constraint: str
    selected_option_contracts: tuple[str, ...] = field(default_factory=tuple)
    equity_features_used: tuple[str, ...] = field(default_factory=tuple)
    option_features_used: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if not 0.0 <= self.fill_probability_stock <= 1.0:
            raise ValueError("fill_probability_stock must be in [0, 1]")
        if not 0.0 <= self.fill_probability_option <= 1.0:
            raise ValueError("fill_probability_option must be in [0, 1]")
        if self.max_loss_stock < 0:
            raise ValueError("max_loss_stock must be >= 0")
        if self.max_loss_option < 0:
            raise ValueError("max_loss_option must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StockOptionRouteDecision:
    underlying_symbol: str
    session_date: str
    decision_timestamp_ns: int
    final_route_decision: str
    payoff: StockOptionPayoffComparison
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    ontology_claim_ids: tuple[str, ...] = field(default_factory=tuple)
    pdf_citations: tuple[str, ...] = field(default_factory=tuple)
    leakage_status: str = "CLEAN"
    rejection_reason: str | None = None

    def validate(self) -> None:
        if self.final_route_decision not in VALID_ROUTES:
            raise ValueError(
                f"final_route_decision must be one of {sorted(VALID_ROUTES)}; "
                f"got {self.final_route_decision!r}"
            )
        self.payoff.validate()
        if self.leakage_status not in ("CLEAN", "REJECTED"):
            raise ValueError("leakage_status must be CLEAN or REJECTED")
        if self.leakage_status == "REJECTED" and not self.rejection_reason:
            raise ValueError("rejection_reason required when leakage_status=REJECTED")
        if self.leakage_status == "CLEAN" and not self.ontology_claim_ids:
            raise ValueError(
                "Clean route decisions must have at least one ontology_claim_id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying_symbol": self.underlying_symbol,
            "session_date": self.session_date,
            "decision_timestamp_ns": self.decision_timestamp_ns,
            "final_route_decision": self.final_route_decision,
            "payoff": self.payoff.to_dict(),
            "reason_codes": list(self.reason_codes),
            "ontology_claim_ids": list(self.ontology_claim_ids),
            "pdf_citations": list(self.pdf_citations),
            "leakage_status": self.leakage_status,
            "rejection_reason": self.rejection_reason,
        }


def build_default_citations(route: str) -> list[dict]:
    """Return default citations for a route. Used by tests and the comparator."""
    return default_citations_for_route(route)
