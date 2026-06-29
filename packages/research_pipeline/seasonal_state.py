"""Seasonal state conditioning scaffold (Phase 5 stub).

Session/seasonality buckets for SEASONAL_STATE_CONDITIONED_MICRO_ALPHA are deferred;
this module defines the acceptance shell only.
"""

from __future__ import annotations

from typing import Any, Sequence

SEASONAL_STATE_FEATURE_NAMES: tuple[str, ...] = (
    "seasonal_state_weight",
    "session_seasonality_bucket",
    "day_of_week_effect",
    "month_of_year_effect",
)


def empty_seasonal_state_shell(*, relationship_id: str = "seasonal_state:standalone") -> dict[str, Any]:
    """Return Phase 5 seasonal_state feature-group shell."""
    return {
        "group_id": "seasonal_state",
        "relationship_id": relationship_id,
        "feature_names": list(SEASONAL_STATE_FEATURE_NAMES),
        "row_count": 0,
        "missingness_ratio": None,
        "pit_proof": "pending",
    }


def assert_seasonal_state_feature_names(feature_names: Sequence[str]) -> None:
    """Ensure declared names stay within the seasonal-state taxonomy stub."""
    allowed = set(SEASONAL_STATE_FEATURE_NAMES)
    for name in feature_names:
        if str(name) not in allowed:
            raise ValueError(f"unknown_seasonal_state_feature:{name}")
