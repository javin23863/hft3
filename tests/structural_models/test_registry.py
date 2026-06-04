"""Registry inventory: 11 PDF models + 45 HYP."""

from features_engine.src.hypotheses.registry import HypothesisRegistry, get_active_hypotheses
from features_engine.src.structural_models.registry import (
    MODEL_DEPENDENCY_MAP,
    PDF_MODEL_IDS,
    get_structural_models,
)


def test_eleven_pdf_models_registered():
    models = get_structural_models()
    assert len(models) == 11
    ids = {m.model_id for m in models}
    assert ids == set(PDF_MODEL_IDS)


def test_hft_framework_models_present():
    ids = set(PDF_MODEL_IDS)
    assert "TRANSFER_ENTROPY" in ids
    assert "HAWKES_TOXIC_FLOW" in ids


def test_dependency_map():
    assert MODEL_DEPENDENCY_MAP["HYBRID_EXECUTION"] == ["BOOK_PRESSURE", "VPIN_TOXICITY"]
    assert MODEL_DEPENDENCY_MAP["HAWKES_TOXIC_FLOW"] == ["HYBRID_EXECUTION"]
    assert MODEL_DEPENDENCY_MAP["BOOK_PRESSURE"] == []


def test_hypothesis_count():
    reg = HypothesisRegistry()
    assert len(reg.families) == 45


def test_active_hypotheses_not_including_pdf():
    hyps = get_active_hypotheses()
    hyp_ids = {f"HYP_{h.hyp_id}" for h in hyps}
    for pdf_id in PDF_MODEL_IDS:
        assert pdf_id not in hyp_ids
