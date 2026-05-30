"""Tests for PDF_MODEL_5 dealer GEX."""

from features_engine.src.structural_models.model_05_dealer_hedging import (
    DealerHedgingModel,
    bs_gamma,
    dollar_gex,
    find_zero_gamma_level,
)


def test_gamma_positive():
    g = bs_gamma(spot=100.0, strike=100.0, t=0.05, r=0.05, sigma=0.2)
    assert g > 0.0


def test_dollar_gex_scales_with_oi():
    g = bs_gamma(100.0, 100.0, 0.05, 0.05, 0.2)
    gex1 = dollar_gex(g, open_interest=1000, spot=100.0)
    gex2 = dollar_gex(g, open_interest=2000, spot=100.0)
    assert abs(gex2 / gex1 - 2.0) < 1e-9


def test_zero_gamma_crossing():
    gex = {90.0: -100.0, 100.0: 50.0, 110.0: 80.0}
    level = find_zero_gamma_level(gex, spot=100.0)
    assert level is not None


def test_dealer_model_aggregation():
    model = DealerHedgingModel()
    chain = [
        {"strike": 100.0, "iv": 0.2, "open_interest": 1000, "right": "C"},
        {"strike": 105.0, "iv": 0.22, "open_interest": 800, "right": "P"},
    ]
    out = model.evaluate(spot=100.0, chain=chain)
    assert out.payload.total_gex != 0.0
    assert len(out.payload.gex_by_strike) == 2
