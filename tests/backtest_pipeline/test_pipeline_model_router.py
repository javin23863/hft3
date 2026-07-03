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
from features_engine.src.model_registry import all_slugs


def test_all_model_ids_cover_registry() -> None:
    # count derives from the registry loader, never a stale literal
    ids = all_model_ids()
    expected = len(all_slugs())
    assert len(ids) == expected
    assert len(set(ids)) == expected


def test_route_engine_kinds_unique_coverage() -> None:
    kinds = {route(mid).engine_kind for mid in all_model_ids()}
    assert "hyp_mbo" in kinds
    assert "pdf_hybrid_replay" in kinds
    assert "pdf_structural_eval" in kinds
    assert "pdf_diagnostics" in kinds
    assert "pdf_options_fixture" in kinds


def test_smoke_hyp_sample() -> None:
    assert SMOKE_HYP_SAMPLE == {"SECOND_WAVE_CONTINUATION", "SPREAD_BLOWOUT_RECOMPRESSION"}


def test_pdf_model_routes() -> None:
    assert route("HYBRID_EXECUTION").engine_kind == "pdf_hybrid_replay"
    assert route("BOOK_PRESSURE").engine_kind == "pdf_structural_eval"
    assert route("TREASURY_CTD").engine_kind == "pdf_diagnostics"
    assert route("DEALER_HEDGING").engine_kind == "pdf_options_fixture"
    assert "HYBRID_EXECUTION" in PDF_HYBRID_REPLAY
    assert len(PDF_STRUCTURAL_EVAL) == 6
    assert len(PDF_DIAGNOSTICS) == 3


def test_legacy_ids_resolve_via_route() -> None:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert route("PDF_MODEL_4").engine_kind == "pdf_hybrid_replay"
        assert route("HYP_1").engine_kind == "hyp_mbo"
        assert route("PDF_MODEL_8").engine_kind == "pdf_structural_eval"
