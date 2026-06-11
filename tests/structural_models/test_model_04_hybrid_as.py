"""Tests for PDF_MODEL_4 hybrid Avellaneda-Stoikov execution."""

import math

from features_engine.src.structural_models.model_04_hybrid_execution import (
    HybridExecutionModel,
    as_optimal_spread,
    as_reservation_price,
    hybrid_reservation,
)
from features_engine.src.structural_models.types import BookPressureOutput, VPINToxicityOutput


def test_as_reservation_price():
    r = as_reservation_price(mid=100.0, inventory=2.0, gamma=0.1, sigma=0.02, time_remaining=3600.0)
    assert r < 100.0


def test_as_spread_positive():
    spread = as_optimal_spread(gamma=0.1, kappa=1.5, sigma=0.02, time_remaining=3600.0)
    assert spread > 0.0


def test_spread_matches_as2008_closed_form():
    gamma, kappa, sigma, t = 0.1, 1.5, 2.0, 0.5
    expected = gamma * sigma ** 2 * t + (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    result = as_optimal_spread(gamma=gamma, kappa=kappa, sigma=sigma, time_remaining=t)
    assert math.isclose(result, expected, rel_tol=1e-12)


def test_spread_widens_with_volatility():
    kwargs = dict(gamma=0.1, kappa=1.5, time_remaining=0.5)
    s0 = as_optimal_spread(sigma=0.0, **kwargs)
    s1 = as_optimal_spread(sigma=1.0, **kwargs)
    s3 = as_optimal_spread(sigma=3.0, **kwargs)
    assert s3 > s1 > s0


def test_sigma_zero_reduces_to_pure_glft_term():
    gamma, kappa = 0.1, 1.5
    expected = (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    result = as_optimal_spread(gamma=gamma, kappa=kappa, sigma=0.0, time_remaining=0.5)
    assert math.isclose(result, expected, rel_tol=1e-12)


def test_time_remaining_clamped():
    kwargs = dict(gamma=0.1, kappa=1.5, sigma=2.0)
    assert as_optimal_spread(time_remaining=-5.0, **kwargs) == as_optimal_spread(time_remaining=0.0, **kwargs)


def test_hybrid_adds_ofi_drift():
    base = 99.5
    hybrid, drift, mult = hybrid_reservation(base, ofi_smooth=10.0, vpin_value=0.2, lambda_scale=0.01)
    assert hybrid == base + drift
    assert drift == 0.01 * (1.0 + 0.2) * 10.0


def test_hybrid_execution_with_mocked_inputs():
    model = HybridExecutionModel()
    book = BookPressureOutput(OFI_smooth=5.0)
    vpin = VPINToxicityOutput(VPIN_value=0.3)
    out = model.evaluate(
        mid=4500.0,
        inventory=1.0,
        book_pressure=book,
        vpin=vpin,
    )
    p = out.payload
    assert p.optimal_bid < p.hybrid_reservation_price < p.optimal_ask
    assert p.spread_width > 0.0
    assert p.OFI_drift_component != 0.0
    assert p.VPIN_multiplier > 1.0
