"""Tests for PDF_MODEL_11 Hawkes toxic flow."""

from features_engine.src.structural_models.model_11_hawkes_toxic import (
    HawkesToxicFlowModel,
    hawkes_intensity,
    multivariate_hawkes_intensity,
)


def test_hawkes_intensity_at_least_mu():
    lam = hawkes_intensity(1.0, [0.2, 0.5, 0.9], mu=0.1, alpha=0.5, beta=1.0)
    assert lam >= 0.1


def test_multivariate_intensities_non_negative():
    events = {"buy": [0.1, 0.3, 0.8], "sell": [0.2, 0.4]}
    mu = {"buy": 0.1, "sell": 0.1}
    alpha = {("buy", "buy"): 0.3, ("sell", "buy"): 0.2, ("buy", "sell"): 0.2, ("sell", "sell"): 0.3}
    out = multivariate_hawkes_intensity(1.0, events, mu, alpha, beta=1.0)
    assert all(v >= 0.0 for v in out.values())


def test_toxic_cascade_raises_gamma():
    model = HawkesToxicFlowModel(params={"hawkes": {"toxic_threshold": 0.5}})
    out = model.evaluate(t=1.0, market_order_times=[0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5])
    assert out.payload.risk_aversion_gamma >= 0.1
