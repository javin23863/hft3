"""Tests for PDF structural model outputs — verifies each model produces valid typed outputs.

Traces to: algorithmic_trading_strategy_development.pdf (PDF_MODEL_1..7),
hft_framework_developer_prompt.pdf (PDF_MODEL_8..11).
"""
from __future__ import annotations

import numpy as np
import pytest

from features_engine.src.structural_models.registry import get_structural_models
from features_engine.src.structural_models.types import (
    BookPressureOutput,
    CrossAssetLeadLagOutput,
    DealerHedgingOutput,
    DowYMIndexOutput,
    HawkesToxicOutput,
    HybridExecutionOutput,
    QuantumSpreadOutput,
    StochasticThermoOutput,
    TransferEntropyOutput,
    TreasuryCTDOutput,
    VPINToxicityOutput,
)

OUTPUT_TYPES = {
    "BOOK_PRESSURE": BookPressureOutput,
    "CROSS_ASSET_LEAD_LAG": CrossAssetLeadLagOutput,
    "VPIN_TOXICITY": VPINToxicityOutput,
    "HYBRID_EXECUTION": HybridExecutionOutput,
    "DEALER_HEDGING": DealerHedgingOutput,
    "DOW_YM_INDEX": DowYMIndexOutput,
    "TREASURY_CTD": TreasuryCTDOutput,
    "TRANSFER_ENTROPY": TransferEntropyOutput,
    "QUANTUM_SPREAD_DEFENSE": QuantumSpreadOutput,
    "STOCHASTIC_THERMO": StochasticThermoOutput,
    "HAWKES_TOXIC_FLOW": HawkesToxicOutput,
}


class TestStructuralModelOutputTypes:
    def test_all_models_loaded(self):
        models = get_structural_models()
        assert len(models) == 11, f"Expected 11 PDF models, got {len(models)}"

    def test_each_model_has_known_id(self):
        models = get_structural_models()
        for model in models:
            mid = getattr(model, "model_id", "")
            assert mid in OUTPUT_TYPES, f"Unknown model_id: {mid}"

    def test_book_pressure_produces_valid_output(self):
        models = get_structural_models()
        bp = next((m for m in models if getattr(m, "model_id", "") == "BOOK_PRESSURE"), None)
        assert bp is not None, "BOOK_PRESSURE model not found"
        result = bp.evaluate(bid_p=100.0, bid_q=50, ask_p=100.25, ask_q=30)
        assert result is not None
        assert isinstance(result.payload, BookPressureOutput)
        assert result.payload.OFI_value != 0.0
        assert isinstance(result.payload.spoofing_risk_flag, bool)

    def test_vpin_produces_valid_output(self):
        models = get_structural_models()
        vpin = next((m for m in models if getattr(m, "model_id", "") == "VPIN_TOXICITY"), None)
        assert vpin is not None
        result = vpin.evaluate(mid=100.0, volume=100.0)
        assert result is not None
        assert isinstance(result.payload, VPINToxicityOutput)
        assert result.payload.VPIN_value >= 0.0
        assert result.payload.toxicity_regime in ("normal", "elevated", "toxic")

    def test_hybrid_execution_produces_valid_output(self):
        models = get_structural_models()
        hybrid = next((m for m in models if getattr(m, "model_id", "") == "HYBRID_EXECUTION"), None)
        assert hybrid is not None
        result = hybrid.evaluate(mid=100.0, inventory=0.0, sigma=0.02)
        assert result is not None
        assert isinstance(result.payload, HybridExecutionOutput)
        assert abs(result.payload.hybrid_reservation_price - 100.0) < 0.1

    def test_quantum_spread_produces_valid_output(self):
        models = get_structural_models()
        qs = next((m for m in models if getattr(m, "model_id", "") == "QUANTUM_SPREAD_DEFENSE"), None)
        assert qs is not None
        result = qs.evaluate(spread_ticks=2.0)
        assert result is not None
        assert isinstance(result.payload, QuantumSpreadOutput)
        assert result.payload.spread_probability >= 0.0
        assert isinstance(result.payload.cancel_all_quotes, bool)

    def test_quantum_spread_does_not_require_numpy_trapz(self, monkeypatch):
        from features_engine.src.structural_models import model_09_quantum_spread as quantum_spread

        calls = {"trapezoid": 0}

        def fake_trapezoid(y, x):
            calls["trapezoid"] += 1
            return float(np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1]) * 0.5))

        monkeypatch.setattr(quantum_spread.np, "trapezoid", fake_trapezoid, raising=False)
        monkeypatch.delattr(quantum_spread.np, "trapz", raising=False)

        result = quantum_spread.QuantumSpreadDefenseModel().evaluate(spread_ticks=2.0)

        assert result.payload.spread_probability >= 0.0
        assert calls["trapezoid"] > 0

    def test_hawkes_toxic_produces_valid_output(self):
        models = get_structural_models()
        hk = next((m for m in models if getattr(m, "model_id", "") == "HAWKES_TOXIC_FLOW"), None)
        assert hk is not None
        result = hk.evaluate(t=1.0, market_order_times=[])
        assert result is not None
        assert isinstance(result.payload, HawkesToxicOutput)
        assert result.payload.toxic_cascade_score >= 0.0
        assert isinstance(result.payload.toxic_flow_detected, bool)

    def test_transfer_entropy_produces_valid_output(self):
        models = get_structural_models()
        te = next((m for m in models if getattr(m, "model_id", "") == "TRANSFER_ENTROPY"), None)
        assert te is not None
        result = te.evaluate(leader_asset="ES", target_asset="MES", leader_returns=[], target_returns=[])
        assert result is not None
        assert isinstance(result.payload, TransferEntropyOutput)
        assert result.payload.transfer_entropy >= 0.0

    def test_dealer_hedging_produces_valid_output(self):
        models = get_structural_models()
        dh = next((m for m in models if getattr(m, "model_id", "") == "DEALER_HEDGING"), None)
        assert dh is not None
        result = dh.evaluate(spot=100.0, chain=[])
        assert result is not None
        assert isinstance(result.payload, DealerHedgingOutput)

    def test_stochastic_thermo_produces_valid_output(self):
        models = get_structural_models()
        st = next((m for m in models if getattr(m, "model_id", "") == "STOCHASTIC_THERMO"), None)
        assert st is not None
        result = st.evaluate()
        assert result is not None
        assert isinstance(result.payload, StochasticThermoOutput)
        assert result.payload.partition_function >= 1.0

    def test_structural_integrator_feature_slots(self):
        from features_engine.src.pipeline.structural_integration import (
            StructuralModelIntegrator,
            STRUCTURAL_FEATURE_START,
            STRUCTURAL_FEATURE_COUNT,
        )
        from features_engine.src.features.mbo_features import MBOEvent
        integrator = StructuralModelIntegrator(tick_size=0.25)
        vec = np.zeros(64, dtype=np.float64)
        ts = 1_700_000_000_000_000_000

        snap1 = integrator.integrate(
            MBOEvent(ts, 1, "ADD", "B", 100.0, 50), vec
        )
        assert len(vec) >= STRUCTURAL_FEATURE_START + STRUCTURAL_FEATURE_COUNT
        assert snap1.book_pressure is not None
        assert abs(vec[STRUCTURAL_FEATURE_START]) > 0
        assert not np.isnan(vec[STRUCTURAL_FEATURE_START])
