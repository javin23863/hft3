"""Cross-asset assembly and no-placeholder structural tests (Phase 2)."""

from __future__ import annotations

import numpy as np

from features_engine.src.features.mbo_features import MBOEvent
from features_engine.src.hypotheses.modules import EsToMesLeadLag, MarketState
from features_engine.src.pipeline.structural_integration import StructuralModelIntegrator
from features_engine.src.structural_models.types import BookPressureOutput
from replay.cross_asset_assembly import (
    enrich_cross_leg,
    is_own_symbol_placeholder,
    validate_cross_asset_alignment,
)


def test_enrich_cross_leg_adds_provenance() -> None:
    leg = enrich_cross_leg({"aggressor_volume_imbalance": 0.2}, symbol="ES", source_timestamp_ns=1000)
    assert leg["_symbol"] == "ES"
    assert leg["_source_timestamp_ns"] == 1000


def test_validate_rejects_missing_leader_leg() -> None:
    result = validate_cross_asset_alignment({}, target_symbol="MES", required_leaders=("ES",))
    assert not result.ok
    assert "ES" in result.missing_leaders


def test_validate_accepts_real_leader_leg() -> None:
    cross = {
        "ES": enrich_cross_leg({"aggressor_volume_imbalance": 0.3}, symbol="ES", source_timestamp_ns=900),
    }
    result = validate_cross_asset_alignment(
        cross,
        target_symbol="MES",
        required_leaders=("ES",),
        decision_timestamp_ns=1000,
    )
    assert result.ok
    assert result.present_leaders == ("ES",)


def test_validate_rejects_future_source_timestamp() -> None:
    cross = {
        "ES": enrich_cross_leg({"aggressor_volume_imbalance": 0.3}, symbol="ES", source_timestamp_ns=2000),
    }
    result = validate_cross_asset_alignment(
        cross,
        target_symbol="MES",
        required_leaders=("ES",),
        decision_timestamp_ns=1000,
    )
    assert not result.ok
    assert any("future_source_timestamp" in r for r in result.reasons)


def test_own_symbol_placeholder_detected() -> None:
    assert is_own_symbol_placeholder(
        target_symbol="MES",
        leader_symbol="MES",
        cross_asset_features={"MES": {"aggressor_volume_imbalance": 0.1}},
    )


def test_structural_integrator_skips_cross_asset_without_leader_books() -> None:
    integrator = StructuralModelIntegrator(target_asset="MES", leader_asset="ES")
    vec = np.zeros(64)
    event = MBOEvent(timestamp_ns=1, order_id=1, action="ADD", side="B", price=5000.0, size=1)
    snapshot = integrator.integrate(event, vec)
    assert snapshot.cross_asset is None
    assert vec[59] == 0.0


def test_structural_integrator_uses_distinct_leader_and_target_ofi() -> None:
    integrator = StructuralModelIntegrator(target_asset="MES", leader_asset="ES")
    integrator.set_cross_asset_book_pressure(
        {
            "MES": BookPressureOutput(OFI_value=0.1, OFI_smooth=0.1),
            "ES": BookPressureOutput(OFI_value=0.5, OFI_smooth=0.5),
        },
        target_asset="MES",
        leader_asset="ES",
    )
    vec = np.zeros(64)
    event = MBOEvent(timestamp_ns=1, order_id=1, action="ADD", side="B", price=5000.0, size=1)
    snapshot = integrator.integrate(event, vec)
    assert snapshot.cross_asset is not None
    assert snapshot.cross_asset.leader_asset == "ES"
    assert snapshot.cross_asset.target_asset == "MES"


def test_es_to_mes_abstains_without_leader_leg() -> None:
    hyp = EsToMesLeadLag()
    state = MarketState(
        primary_features={"aggressor_volume_imbalance": 0.2},
        cross_asset_features={},
        regime_state="normal",
        event_context="REGULAR",
        volatility_state="LOW",
        liquidity_state="NORMAL",
        latency_ms=1.0,
        current_inventory=0,
    )
    assert hyp.evaluate(state) == 0.0


def test_target_default_leaders_cover_russell_and_dow_lanes() -> None:
    from replay.cross_asset_assembly import TARGET_DEFAULT_LEADERS

    assert TARGET_DEFAULT_LEADERS["RTY"] == ("ES",)
    assert TARGET_DEFAULT_LEADERS["YM"] == ("ES",)
    assert TARGET_DEFAULT_LEADERS["M2K"] == ("RTY",)
    assert TARGET_DEFAULT_LEADERS["MYM"] == ("YM",)


def test_required_leaders_for_model_names_hypothesis_leader_tapes() -> None:
    from replay.cross_asset_assembly import required_leaders_for_model

    assert required_leaders_for_model("ES_MES_LEAD_LAG") == ("ES",)
    assert required_leaders_for_model("NQ_MNQ_LEAD_LAG") == ("NQ",)
    assert required_leaders_for_model("ES_NQ_DIVERGENCE_SNAPBACK") == ("ES", "NQ")
    assert required_leaders_for_model("ZN_ZB_ES_NQ_MACRO_IMPULSE") == ("ZN",)
    assert required_leaders_for_model("MICRO_CONTRACT_RETAIL_LAG") == ("ES",)
    assert required_leaders_for_model("SPREAD_BLOWOUT_RECOMPRESSION") == ()
    assert required_leaders_for_model("") == ()
