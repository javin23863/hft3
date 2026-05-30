"""Tests for PDF_MODEL_6 Dow/YM index."""

from features_engine.src.structural_models.model_06_dow_ym_index import (
    DowYMIndexModel,
    component_weights,
    price_weighted_index,
)
from features_engine.src.structural_models.types import BookPressureOutput


def test_price_weighted_index():
    idx = price_weighted_index([100.0, 200.0, 300.0], divisor=0.15)
    assert abs(idx - 4000.0) < 1e-9


def test_component_weights_sum_to_one():
    w = component_weights({"A": 100.0, "B": 200.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["B"] > w["A"]


def test_dow_ym_with_model1_ofi():
    model = DowYMIndexModel()
    book_by = {
        "UNH": BookPressureOutput(OFI_smooth=2.0),
        "GS": BookPressureOutput(OFI_smooth=-1.0),
    }
    out = model.evaluate(book_pressure_by_asset=book_by, top_n=2)
    assert "UNH" in out.payload.top_component_OFI
    assert out.payload.synthetic_Dow_pressure != 0.0
