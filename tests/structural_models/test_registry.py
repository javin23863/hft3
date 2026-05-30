"""Registry inventory: 7 PDF models + 44 HYP unchanged."""

from features_engine.src.hypotheses.registry import HypothesisRegistry, get_active_hypotheses
from features_engine.src.structural_models.registry import (
    MODEL_DEPENDENCY_MAP,
    PDF_MODEL_IDS,
    get_structural_models,
)


def test_seven_pdf_models_registered():
    models = get_structural_models()
    assert len(models) == 7
    ids = {m.model_id for m in models}
    assert ids == set(PDF_MODEL_IDS)


def test_dependency_map():
    assert MODEL_DEPENDENCY_MAP["PDF_MODEL_4"] == ["PDF_MODEL_1", "PDF_MODEL_3"]
    assert MODEL_DEPENDENCY_MAP["PDF_MODEL_1"] == []


def test_hypothesis_count_unchanged():
    reg = HypothesisRegistry()
    assert len(reg.families) == 44


def test_active_hypotheses_not_including_pdf():
    hyps = get_active_hypotheses()
    hyp_ids = {f"HYP_{h.hyp_id}" for h in hyps}
    for pdf_id in PDF_MODEL_IDS:
        assert pdf_id not in hyp_ids
