"""CME continuous universe profiles (Phase 1)."""

from __future__ import annotations

import re
from typing import Any

UNIVERSE_PROFILES = frozenset({"full_cme_research", "pilot_liquidity_top"})

# Appendix B pilot liquidity roots (Phase 1 filter set).
PILOT_LIQUIDITY_ROOTS = frozenset(
    {
        "MES",
        "ES",
        "MNQ",
        "NQ",
        "MGC",
        "GC",
        "MCL",
        "CL",
        "SI",
        "HG",
        "RB",
        "HO",
        "NG",
        "ZT",
        "ZF",
        "ZN",
        "ZB",
        "UB",
    }
)

_CONTRACT_ROOT_RE = re.compile(r"^([A-Z]{2,4})[FGHJKMNQUVXZ]\d+$")

DEFAULT_MAX_MISSING_RATIO = 0.25
DEFAULT_MIN_LIQUIDITY_SCORE = 0.1
LIQUIDITY_ROW_REFERENCE = 10_000


def validate_universe_profile(profile: str) -> str:
    if profile not in UNIVERSE_PROFILES:
        raise ValueError(f"unknown universe profile: {profile!r}")
    return profile


def contract_root_symbol(contract: str) -> str:
    """Extract CME root symbol from a contract id (e.g. ESM6 -> ES)."""
    token = contract.split(".")[0].strip().upper()
    match = _CONTRACT_ROOT_RE.match(token)
    if match:
        return match.group(1)
    return token


def is_active_for_profile(contract: str, profile: str) -> bool:
    """Return whether *contract* belongs in the universe profile."""
    validate_universe_profile(profile)
    if profile == "full_cme_research":
        return True
    return contract_root_symbol(contract) in PILOT_LIQUIDITY_ROOTS


def filter_contracts_for_profile(contracts: list[str], profile: str) -> list[str]:
    """Keep contracts whose root symbol is active for *profile*."""
    return [c for c in contracts if is_active_for_profile(c, profile)]


def passes_coverage_filter(
    missing_ratio: float | None,
    *,
    max_missing_ratio: float = DEFAULT_MAX_MISSING_RATIO,
) -> bool:
    if missing_ratio is None:
        return False
    return missing_ratio <= max_missing_ratio


def passes_liquidity_filter(
    liquidity_score: float | None,
    *,
    min_liquidity_score: float = DEFAULT_MIN_LIQUIDITY_SCORE,
) -> bool:
    if liquidity_score is None:
        return False
    return liquidity_score >= min_liquidity_score


def stub_liquidity_score(row_count: int) -> float | None:
    """Phase 1 stub: normalize event rows to [0, 1] against a reference floor."""
    if row_count <= 0:
        return None
    return min(1.0, row_count / LIQUIDITY_ROW_REFERENCE)


def compute_eligibility(
    *,
    contract: str,
    missing_ratio: float | None,
    liquidity_score: float | None,
    universe_profile: str,
    max_missing_ratio: float = DEFAULT_MAX_MISSING_RATIO,
    min_liquidity_score: float = DEFAULT_MIN_LIQUIDITY_SCORE,
) -> bool | None:
    """Eligibility stub with real structure; None when profile excludes contract."""
    if not is_active_for_profile(contract, universe_profile):
        return None
    if missing_ratio is None or liquidity_score is None:
        return False
    return passes_coverage_filter(
        missing_ratio, max_missing_ratio=max_missing_ratio
    ) and passes_liquidity_filter(liquidity_score, min_liquidity_score=min_liquidity_score)


def profile_filter_thresholds(profile: str) -> dict[str, float]:
    """Per-profile coverage/liquidity thresholds (Phase 1 defaults)."""
    validate_universe_profile(profile)
    if profile == "pilot_liquidity_top":
        return {
            "max_missing_ratio": 0.20,
            "min_liquidity_score": 0.15,
        }
    return {
        "max_missing_ratio": DEFAULT_MAX_MISSING_RATIO,
        "min_liquidity_score": DEFAULT_MIN_LIQUIDITY_SCORE,
    }


def apply_profile_to_contract_row(row: dict[str, Any], profile: str) -> dict[str, Any]:
    """Attach eligibility for *profile* using row missing_ratio / liquidity_score."""
    thresholds = profile_filter_thresholds(profile)
    eligible = compute_eligibility(
        contract=str(row.get("contract") or ""),
        missing_ratio=row.get("missing_ratio"),
        liquidity_score=row.get("liquidity_score"),
        universe_profile=profile,
        **thresholds,
    )
    return {**row, "eligible": eligible}
