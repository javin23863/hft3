"""Phase 4 continuous CME model registry and lane parser tests."""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "packages") not in sys.path:
    sys.path.insert(0, str(REPO / "packages"))


def test_continuous_eligible_registry_entries() -> None:
    from features_engine.src.model_registry import (
        continuous_eligible_slugs,
        get_continuous_model_entry,
    )

    slugs = continuous_eligible_slugs()
    assert len(slugs) == 11
    assert "MICRO_STANDARD_FLOW_TRANSFER" in slugs
    assert "RL_EXECUTION_OVERLAY" in slugs

    entry = get_continuous_model_entry("CROSS_MARKET_OFI_IMPACT")
    assert entry["kind"] == "continuous_microstructure"
    assert entry["continuous_eligible"] is True
    assert entry["event_eligible"] is False
    assert entry["model_family"] == "cross_asset_flow"
    assert entry["requires_relationship_graph"] is True


def test_get_continuous_model_entry_rejects_event_hypothesis() -> None:
    from features_engine.src.model_registry import get_continuous_model_entry

    with pytest.raises(KeyError, match="not continuous-eligible"):
        get_continuous_model_entry("SPREAD_BLOWOUT_RECOMPRESSION")


def test_parse_continuous_lane_profile_returns_family() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    profile = parse_continuous_lane_profile(
        "Cross-market OFI impact on GC to SI lead-lag",
        universe_profile="full_cme_research",
        use_llm=False,
    )
    assert profile.lane == "continuous_microstructure"
    assert profile.primary_model_id == "CROSS_MARKET_OFI_IMPACT"
    assert profile.model_family == "cross_asset_flow"
    assert profile.universe_profile == "full_cme_research"
    assert profile.relationship_family is None  # multi-family; disambiguated in Phase 5
    assert profile.param_ranges["ofi_beta_lag"] == [1.0, 10.0]


def test_parse_continuous_lane_profile_slug_in_parens() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    profile = parse_continuous_lane_profile(
        "Pilot (BOOK_RESILIENCY_CONTINUATION) on MES",
        use_llm=False,
    )
    assert profile.primary_model_id == "BOOK_RESILIENCY_CONTINUATION"
    assert profile.model_family == "liquidity_resiliency"
    assert profile.relationship_family == "micro_standard"


def test_parse_continuous_lane_profile_rl_overlay_no_relationship_family() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    profile = parse_continuous_lane_profile(
        "RL execution overlay for cost reduction",
        use_llm=False,
    )
    assert profile.primary_model_id == "RL_EXECUTION_OVERLAY"
    assert profile.relationship_family is None


def test_parse_continuous_lane_profile_rejects_event_slug_in_parens() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    with pytest.raises(ValueError, match="not continuous-eligible"):
        parse_continuous_lane_profile(
            "Pilot (SPREAD_BLOWOUT_RECOMPRESSION) on MES",
            use_llm=False,
        )


def test_parse_continuous_lane_profile_unmatched_raises() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile

    with pytest.raises(ValueError, match="cannot infer continuous model"):
        parse_continuous_lane_profile("random thesis with no keywords", use_llm=False)


def test_parse_continuous_lane_profile_no_universe_expansion() -> None:
    from research_pipeline.hypothesis_parser import parse_continuous_lane_profile
    from research_pipeline.types import ContinuousLaneProfile

    profile = parse_continuous_lane_profile(
        "Seasonal state micro alpha",
        universe_profile="full_cme_research",
        use_llm=False,
    )
    assert profile.primary_model_id == "SEASONAL_STATE_CONDITIONED_MICRO_ALPHA"
    assert profile.universe_profile == "full_cme_research"
    field_names = {f.name for f in fields(ContinuousLaneProfile)}
    assert "instrument_universe" not in field_names


def test_event_parse_rejects_continuous_slug_in_parens() -> None:
    from research_pipeline.hypothesis_parser import parse_hypothesis

    with pytest.raises(ValueError, match="continuous-eligible"):
        parse_hypothesis(
            "Fade spread after (MICRO_STANDARD_FLOW_TRANSFER) on ES",
            use_llm=False,
        )
