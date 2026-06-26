"""Commodity calendar/curve structure scaffold (Phase 5 stub).

Covers metals, energy, and rates curve complexes from cme_relationship_graph.yaml.
Scoring and NPZ-backed curve features are deferred.
"""

from __future__ import annotations

from typing import Any, Sequence

COMMODITY_COMPLEX_FAMILY_IDS: tuple[str, ...] = (
    "metals_complex",
    "energy_complex",
    "rates_curve",
)

COMMODITY_STRUCTURE_FEATURE_NAMES: tuple[str, ...] = (
    "curve_zscore",
    "spread_z_score",
    "cointegration_residual",
    "cost_feasibility",
)

_CROSS_MARKET_COMPLEX_IDS: frozenset[str] = frozenset({"metals_complex", "energy_complex"})
_CALENDAR_CURVE_COMPLEX_IDS: frozenset[str] = frozenset({"rates_curve"})


def group_id_for_commodity_complex(complex_id: str) -> str:
    """Route metals/energy complexes to cross_market; rates only to calendar_curve."""
    validate_commodity_complex_id(complex_id)
    if complex_id in _CROSS_MARKET_COMPLEX_IDS:
        return "cross_market"
    if complex_id in _CALENDAR_CURVE_COMPLEX_IDS:
        return "calendar_curve"
    raise ValueError(f"unknown_commodity_complex:{complex_id}")


def empty_commodity_structure_shell(*, complex_id: str) -> dict[str, Any]:
    """Return Phase 5 commodity-structure shell for one commodity complex."""
    return {
        "group_id": group_id_for_commodity_complex(complex_id),
        "complex_id": complex_id,
        "feature_names": list(COMMODITY_STRUCTURE_FEATURE_NAMES),
        "row_count": 0,
        "missingness_ratio": None,
        "pit_proof": "pending",
    }


def validate_commodity_complex_id(complex_id: str) -> None:
    """Fail closed on unknown commodity complex identifiers."""
    if complex_id not in COMMODITY_COMPLEX_FAMILY_IDS:
        raise ValueError(f"unknown_commodity_complex:{complex_id}")


def assert_commodity_structure_feature_names(feature_names: Sequence[str]) -> None:
    """Ensure declared names stay within the commodity-structure taxonomy stub."""
    allowed = set(COMMODITY_STRUCTURE_FEATURE_NAMES)
    for name in feature_names:
        if str(name) not in allowed:
            raise ValueError(f"unknown_commodity_structure_feature:{name}")
