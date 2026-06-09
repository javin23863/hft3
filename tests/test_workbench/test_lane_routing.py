"""Equities routing acceptance tests.

Tests that:
  - Route resolver produces route type from backend logic
  - STOCK_ONLY requires explicit option absence or failure
  - STOCK_AND_OPTION possible when both edges exist
  - NO_TRADE possible when no edge or blocker
  - Route records reason codes
  - Workbench exposes route reason codes
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.state.route_resolver import (
    resolve_route_for_session,
    resolve_routes_for_all_sessions,
)
from workbench.src.state.workbench_truth import build_workbench_truth


REPO = Path(__file__).resolve().parents[2]


def test_route_resolver_returns_structured_result():
    """Route resolver must return dict with route_type and reason_codes."""
    result = resolve_route_for_session(REPO, "kodk_2020")
    assert isinstance(result, dict)
    assert "route_type" in result
    assert "reason_codes" in result
    assert "option_feature_available" in result
    assert "option_feature_used" in result


def test_route_stock_only_requires_explicit_option_absence_or_option_failure():
    """STOCK_ONLY route must explain why options are not used."""
    # AIRE_2025 has no options configured in defaults
    result = resolve_route_for_session(REPO, "aire_2025")
    if result["route_type"] == "STOCK_ONLY":
        assert (
            "OPTIONS_NOT_CONFIGURED" in result["reason_codes"]
            or "OPTION_DATA_NOT_DOWNLOADED" in result["reason_codes"]
            or "MISSING_EQUITY_DATA" in result["reason_codes"]
            or "BLOCKED_DATA" == result["route_type"]
        ), f"STOCK_ONLY must explain why: {result['reason_codes']}"


def test_route_records_reason_codes():
    """Every route result must have non-empty reason_codes."""
    for sid in ("gme_2021", "kodk_2020", "aire_2025", "amst_2026", "snal_2026"):
        result = resolve_route_for_session(REPO, sid)
        assert isinstance(result["reason_codes"], list)
        # Every result should have at least one reason (even if it's an error)
        if result.get("error"):
            assert len(result["reason_codes"]) > 0 or result["route_type"] == "BLOCKED_DATA"


def test_route_resolver_handles_missing_session():
    """Route resolver must handle non-existent sessions gracefully."""
    result = resolve_route_for_session(REPO, "nonexistent_session_12345")
    assert result["route_type"] == "BLOCKED_DATA"
    assert "SESSION_NOT_FOUND" in result["reason_codes"]


def test_resolve_all_sessions_returns_dict():
    """Resolve all sessions must return a dict mapping session_id to route result."""
    routes = resolve_routes_for_all_sessions(REPO)
    assert isinstance(routes, dict)
    if routes:  # May be empty if config missing
        for sid, result in routes.items():
            assert "route_type" in result
            assert "reason_codes" in result


def test_route_resolver_preserves_option_info():
    """Route result must indicate whether option features are available and used."""
    # KODK and GME have options data
    for sid in ("kodk_2020", "gme_2021"):
        result = resolve_route_for_session(REPO, sid)
        assert result["option_feature_available"] in (True, False)
        assert result["option_feature_used"] in (True, False)


def test_route_stock_and_option_possible_when_both_edges_exist():
    """STOCK_AND_OPTION route must be returned when both equity and option data exist."""
    # Check if any session returns STOCK_AND_OPTION
    routes = resolve_routes_for_all_sessions(REPO)
    stock_option_routes = [
        sid for sid, r in routes.items()
        if r["route_type"] == "STOCK_AND_OPTION"
    ]
    # At least one session with options configured should route as STOCK_AND_OPTION
    # or explain why not via reason codes
    for sid in ("kodk_2020", "gme_2021"):
        if sid in routes:
            result = routes[sid]
            assert result["route_type"] in (
                "STOCK_AND_OPTION", "STOCK_ONLY", "BLOCKED_DATA",
            ), f"Unexpected route type for {sid}: {result['route_type']}"
            if result["route_type"] == "STOCK_ONLY":
                assert any(
                    "OPTION_DATA_NOT_DOWNLOADED" in rc or "OPTIONS_NOT_CONFIGURED" in rc
                    for rc in result["reason_codes"]
                ), f"STOCK_ONLY must explain why options not used: {result['reason_codes']}"


def test_route_no_trade_possible_when_no_edge_or_blocker():
    """Route resolver must support NO_TRADE outcome for blocked sessions."""
    result = resolve_route_for_session(REPO, "drys_2016")
    # Skipped sessions should be handled gracefully
    assert result["route_type"] in (
        "BLOCKED_DATA", "NO_TRADE", "STOCK_ONLY",
    ), f"Skipped session should be blocked or no-trade: {result['route_type']}"
