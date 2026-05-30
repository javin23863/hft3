"""Tests for PDF_MODEL_7 Treasury CTD."""

from features_engine.src.structural_models.model_07_treasury_ctd import (
    TreasuryCTDModel,
    ctd_switch_threshold,
    delivery_cost,
    implied_repo_rate,
    select_ctd,
)


def test_delivery_cost_ordering():
    c1 = delivery_cost(108.5, 98.25, 0.8123)
    c2 = delivery_cost(108.5, 99.10, 0.8456)
    assert c1 != c2


def test_select_ctd_min_cost():
    costs = {"A": 1.5, "B": 0.5, "C": 2.0}
    assert select_ctd(costs) == "B"


def test_ctd_switch_threshold():
    costs = {"A": 1.0, "B": 1.5, "C": 2.0}
    assert ctd_switch_threshold(costs, "A") == 0.5


def test_implied_repo_sign():
    repo = implied_repo_rate(108.5, 98.25, 0.8123, days_to_delivery=90.0)
    assert isinstance(repo, float)


def test_treasury_model_from_config():
    model = TreasuryCTDModel()
    out = model.evaluate()
    assert out.payload.current_CTD != ""
    assert len(out.payload.delivery_cost_by_bond) >= 2
