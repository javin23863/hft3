"""CME continuous universe profiles (Phase 1 scaffold)."""

from __future__ import annotations

UNIVERSE_PROFILES = frozenset({"full_cme_research", "pilot_liquidity_top"})


def validate_universe_profile(profile: str) -> str:
    if profile not in UNIVERSE_PROFILES:
        raise ValueError(f"unknown universe profile: {profile!r}")
    return profile
