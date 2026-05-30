"""Tests for PDF_MODEL_4 hybrid Avellaneda-Stoikov execution."""

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
    spread = as_optimal_spread(gamma=0.1, kappa=1.5)
    assert spread > 0.0


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
