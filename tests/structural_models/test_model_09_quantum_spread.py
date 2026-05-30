"""Tests for PDF_MODEL_9 quantum spread defense."""

from features_engine.src.structural_models.model_09_quantum_spread import (
    QuantumSpreadDefenseModel,
    bessel_i0,
    spread_probability,
)


def test_bessel_i0_at_zero():
    assert abs(bessel_i0(0.0) - 1.0) < 1e-6


def test_spread_probability_non_negative():
    for delta in [0.1, 0.5, 1.0, 2.0]:
        p = spread_probability(delta, xi1=1.0, kappa1=1.0)
        assert p >= 0.0


def test_spread_probability_zero_for_nonpositive_delta():
    assert spread_probability(0.0, 1.0, 1.0) == 0.0
    assert spread_probability(-1.0, 1.0, 1.0) == 0.0


def test_cancel_flag_domain():
    model = QuantumSpreadDefenseModel(params={"quantum_spread": {"collapse_threshold": 0.5}})
    out = model.evaluate(xi1=0.2, kappa1=0.2, spread_ticks=0.25)
    assert 0.0 <= out.payload.collapse_risk <= 1.0
    assert out.payload.spread_probability >= 0.0
