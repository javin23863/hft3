"""Ontology objects for equities + options decision context.

These extend the OpenFoundry ontology for the equities lane. Each object has:
- a frozen dataclass for typed construction
- a `to_dict()` method for canonical serialization
- a `validate()` method that raises on ungrounded or out-of-bounds claims

Citations are tracked via `cite_claim()` in citations.py. Ungrounded claims
(i.e. a route decision with no `ontology_claim_ids` or no `pdf_citations`)
are rejected by `StockOptionRouteDecision.validate()`.
"""
from __future__ import annotations

from .session_context import EquitySessionContext
from .option_snapshot import (
    OptionContractAtDecision,
    OptionChainSnapshotAtDecision,
)
from .payoff import (
    StockOptionPayoffComparison,
    StockOptionRouteDecision,
    ROUTE_STOCK_ONLY,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_NO_TRADE,
)
from .feature_vector import StockOptionFeatureVector
from .float_metadata import FloatMetadataAtSession
from .citations import cite_claim, require_grounding

__all__ = [
    "EquitySessionContext",
    "OptionContractAtDecision",
    "OptionChainSnapshotAtDecision",
    "StockOptionPayoffComparison",
    "StockOptionRouteDecision",
    "StockOptionFeatureVector",
    "FloatMetadataAtSession",
    "cite_claim",
    "require_grounding",
    "ROUTE_STOCK_ONLY",
    "ROUTE_OPTION_ONLY",
    "ROUTE_STOCK_AND_OPTION",
    "ROUTE_NO_TRADE",
]
