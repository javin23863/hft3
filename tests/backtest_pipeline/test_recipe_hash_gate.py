"""Phase 6: VectorBT ↔ HftBacktest feature_recipe_hash gate tests."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario
from backtest_pipeline.src.recipe_hash_gate import (
    extract_feature_recipe_hash_from_promoted_row,
    validate_feature_recipe_hash_handoff,
)


def test_extract_hash_from_promoted_row_top_level() -> None:
    row = {"feature_recipe_hash": "abc123"}
    assert extract_feature_recipe_hash_from_promoted_row(row) == "abc123"


def test_extract_hash_from_vectorbt_results() -> None:
    row = {"vectorbt_results": {"feature_recipe_hash": "def456"}}
    assert extract_feature_recipe_hash_from_promoted_row(row) == "def456"


def test_validate_handoff_accepts_matching_hashes() -> None:
    row = {"feature_recipe_hash": "same_hash"}
    assert validate_feature_recipe_hash_handoff(
        scenario_feature_recipe_hash="same_hash",
        promoted_row=row,
    ) == []


def test_validate_handoff_rejects_mismatch() -> None:
    row = {"feature_recipe_hash": "upstream_hash"}
    reasons = validate_feature_recipe_hash_handoff(
        scenario_feature_recipe_hash="scenario_hash",
        promoted_row=row,
    )
    assert "feature_recipe_hash_mismatch" in reasons


def test_validate_handoff_skips_when_both_missing() -> None:
    assert validate_feature_recipe_hash_handoff(
        scenario_feature_recipe_hash="",
        promoted_row={},
    ) == []


def test_scenario_hash_includes_feature_recipe_hash() -> None:
    common = dict(
        scenario_id="s1",
        upstream_screening_artifact=Path("screen.json"),
        upstream_screening_artifact_hash="h1",
        candidate_id="c1",
        model_id="HYP_5",
        symbol="MES.v.0",
        event_id="E1",
        event_type="screen",
        prepared_data_path=Path("events.npz"),
        prepared_data_hash="pd1",
        source_data_hash="sd1",
        feature_set_id="fs_v1",
        feature_set_hash="fh1",
        research_clock="scheduled_event",
        latency_model_path=Path("latency.json"),
        latency_model_hash="lh1",
        fill_queue_model_path=Path("queue.json"),
        fill_queue_model_hash="qh1",
        fee_model_id="fee1",
        split_scheme_id="split1",
        replay_mode="baseline",
        seed=0,
    )
    a = HftReplayScenario(**common, feature_recipe_hash="hash_a")
    b = HftReplayScenario(**common, feature_recipe_hash="hash_b")
    assert a.scenario_hash() != b.scenario_hash()
