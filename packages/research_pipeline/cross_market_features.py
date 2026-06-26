"""Cross-market feature scaffold (Phase 5 stub).

PIT cross-market features (lagged OFI beta, lead-lag stability) are deferred;
this module defines the acceptance shell and pair validation only.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

CROSS_MARKET_FEATURE_NAMES: tuple[str, ...] = (
    "lagged_ofi_beta",
    "lead_lag_stability",
    "impact_decay_half_life",
    "lagged_correlation",
    "volume_leadership",
    "queue_pressure_divergence",
)


def empty_cross_market_feature_shell(*, relationship_id: str = "unknown:?:->?") -> dict[str, Any]:
    """Return Phase 5 cross_market feature-group shell for one relationship edge."""
    return {
        "group_id": "cross_market",
        "relationship_id": relationship_id,
        "feature_names": list(CROSS_MARKET_FEATURE_NAMES),
        "row_count": 0,
        "missingness_ratio": None,
        "pit_proof": "pending",
    }


def validate_cross_market_pair(source_root: str, target_root: str) -> None:
    """Fail closed when cross-market features would use a self-pair."""
    if str(source_root).upper() == str(target_root).upper():
        raise ValueError(f"cross_market_self_pair:{source_root}->{target_root}")


def assert_cross_market_feature_names(feature_names: Sequence[str]) -> None:
    """Ensure declared names stay within the cross-market taxonomy stub."""
    allowed = set(CROSS_MARKET_FEATURE_NAMES)
    for name in feature_names:
        if str(name) not in allowed:
            raise ValueError(f"unknown_cross_market_feature:{name}")


def merge_cross_market_group(
    matrix: Mapping[str, Any], *, relationship_id: str
) -> dict[str, Any]:
    """Attach one cross_market group shell to a feature-matrix mapping (in-memory)."""
    groups = list(matrix.get("feature_groups") or [])
    shell = empty_cross_market_feature_shell(relationship_id=relationship_id)
    groups.append(shell)
    merged = dict(matrix)
    merged["feature_groups"] = groups
    return merged
