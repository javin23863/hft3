"""Tests for pipeline_model_router."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.pipeline_model_router import (
    PDF_DIAGNOSTICS,
    PDF_HYBRID_REPLAY,
    PDF_OPTIONS_FIXTURE,
    PDF_STRUCTURAL_EVAL,
    SMOKE_HYP_SAMPLE,
    all_model_ids,
    route,
)


def test_all_model_ids_count_55() -> None:
    ids = all_model_ids()
    assert len(ids) == 55
    assert len(set(ids)) == 55


def test_route_engine_kinds_unique_coverage() -> None:
    kinds = {route(mid).engine_kind for mid in all_model_ids()}
    assert "hyp_mbo" in kinds
    assert "pdf_hybrid_replay" in kinds
    assert "pdf_structural_eval" in kinds
    assert "pdf_diagnostics" in kinds
    assert "pdf_options_fixture" in kinds


def test_smoke_hyp_sample() -> None:
    assert SMOKE_HYP_SAMPLE == {"HYP_1", "HYP_5"}


def test_pdf_model_routes() -> None:
    assert route("PDF_MODEL_4").engine_kind == "pdf_hybrid_replay"
    assert route("PDF_MODEL_1").engine_kind == "pdf_structural_eval"
    assert route("PDF_MODEL_7").engine_kind == "pdf_diagnostics"
    assert route("PDF_MODEL_5").engine_kind == "pdf_options_fixture"
    assert "PDF_MODEL_4" in PDF_HYBRID_REPLAY
    assert len(PDF_STRUCTURAL_EVAL) == 6
    assert len(PDF_DIAGNOSTICS) == 3
