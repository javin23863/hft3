"""Tests for unified research pipeline stage registry and artifact stamping."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backtest_pipeline.src.research_pipeline_stages import (
    CHRONOLOGICAL_STAGE_IDS,
    STAGE_1_VECTORBT_SCREEN,
    STAGE_3_HFTBACKTEST_REALISM,
    annotate_promoted_screening_handoffs,
    pipeline_stage_stamp,
    record_lifecycle_pipeline_handoff,
    stamp_artifact,
)
from model_metrics import lifecycle as lc


def test_chronological_stage_ids_order():
    assert CHRONOLOGICAL_STAGE_IDS[0].startswith("stage_0")
    assert CHRONOLOGICAL_STAGE_IDS[-1].startswith("stage_7")
    assert len(CHRONOLOGICAL_STAGE_IDS) == 8


def test_pipeline_stage_stamp_fields():
    stamp = pipeline_stage_stamp(STAGE_1_VECTORBT_SCREEN)
    assert stamp["research_pipeline_stage_id"] == STAGE_1_VECTORBT_SCREEN
    assert "docs/vault/UNIFIED_RESEARCH_PIPELINE.md" in stamp["research_pipeline_ontology_doc"]
    assert stamp["research_pipeline_literature_refs"]


def test_stamp_artifact_mutates_dict():
    artifact = {"run_id": "test"}
    stamp_artifact(artifact, STAGE_3_HFTBACKTEST_REALISM)
    assert artifact["research_pipeline_stage_id"] == STAGE_3_HFTBACKTEST_REALISM


def test_record_lifecycle_handoff_annotates_existing_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lifecycle"))
    lc.apply_transition(
        "HYP_TEST_PIPELINE",
        lc.CANDIDATE,
        trigger="test",
        reason="setup",
        actor="test",
        create=True,
        initial={"hypothesis_id": "HYP_TEST_PIPELINE", "symbol": "MES"},
    )
    ok = record_lifecycle_pipeline_handoff(
        "HYP_TEST_PIPELINE",
        STAGE_1_VECTORBT_SCREEN,
        artifact_path="research_cards/pipeline_runs/x/screening_artifact.json",
    )
    assert ok is True
    rec = lc.get_record("HYP_TEST_PIPELINE")
    assert rec is not None
    assert "pipeline_stage_stage_1_vectorbt_screen" in rec.research_card_links


def test_annotate_promoted_screening_handoffs_no_crash_on_empty():
    annotate_promoted_screening_handoffs({"promoted": []})


def test_filter_result_to_dict_includes_pipeline_stage():
    from backtest_pipeline.src.vectorbt_adapter import FilterResult

    result = FilterResult(
        run_id="unit_test",
        code_commit="deadbeef",
        screening_backend="vectorbt",
        vectorbt_available=True,
        backend="vectorbt",
        total_candidates=0,
        max_trials=1,
        trials_run=0,
        max_models=1,
        max_symbols=1,
        max_feature_sets=1,
        max_total_trials=1,
        parameter_space_id="ps_test",
        parameter_space_hash="abc",
        feature_set_id="fs_test",
        feature_set_hash="def",
        data_manifest_hash="ghi",
        lake_manifest_hash="jkl",
    )
    payload = result.to_dict()
    assert payload["research_pipeline_stage_id"] == STAGE_1_VECTORBT_SCREEN
    assert "research_pipeline_ontology_doc" in payload
