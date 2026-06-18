"""Tests for closed three-category research_clock enum (Gate 8)."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import pytest

from backtest_pipeline.src.research_clock import (
    RESEARCH_CLOCK_CONTINUOUS_INTRADAY,
    RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT,
    RESEARCH_CLOCK_SCHEDULED_EVENT,
    ResearchClockError,
    canonicalize_research_clock,
    research_clock_validation_errors,
    validate_research_clock,
)


class TestCanonicalValues:
    def test_scheduled_event(self):
        assert canonicalize_research_clock("scheduled_event") == RESEARCH_CLOCK_SCHEDULED_EVENT

    def test_context_feature_uplift(self):
        assert canonicalize_research_clock("context_feature_uplift") == RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT

    def test_continuous_intraday(self):
        assert canonicalize_research_clock("continuous_intraday") == RESEARCH_CLOCK_CONTINUOUS_INTRADAY

    def test_hyphenated_aliases_normalize(self):
        assert canonicalize_research_clock("scheduled-event") == RESEARCH_CLOCK_SCHEDULED_EVENT
        assert canonicalize_research_clock("context-uplift") == RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT

    def test_legacy_event_window_pilot_alias(self):
        assert canonicalize_research_clock("event_window_pilot") == RESEARCH_CLOCK_SCHEDULED_EVENT

    def test_legacy_context_uplift_alias(self):
        assert canonicalize_research_clock("context_uplift") == RESEARCH_CLOCK_CONTEXT_FEATURE_UPLIFT


class TestFailClosed:
    def test_empty_rejected(self):
        with pytest.raises(ResearchClockError, match="research_clock_empty"):
            canonicalize_research_clock("")

    def test_unknown_rejected(self):
        with pytest.raises(ResearchClockError, match="research_clock_invalid"):
            canonicalize_research_clock("mixed_opportunity_lane")

    def test_validation_errors_token(self):
        assert research_clock_validation_errors("bogus_clock") == [
            "research_clock_invalid"
        ]

    def test_validate_research_clock_includes_context(self):
        with pytest.raises(ResearchClockError, match="screening_artifact.research_clock"):
            validate_research_clock("bogus", context="screening_artifact.research_clock")
