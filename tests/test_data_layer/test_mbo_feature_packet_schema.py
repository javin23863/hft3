"""Tests for the MBO feature packet schema contract."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKET_DIR = REPO / "packages" / "data_layer" / "packet"

jsonschema = pytest.importorskip("jsonschema")

from data_layer.packet.validate import validate_mbo_feature_packet  # noqa: E402


def _load_schema() -> dict:
    return json.loads((PACKET_DIR / "schema_mbo_feature_packet_v1.json").read_text(encoding="utf-8"))


def _sample_packet() -> dict:
    return {
        "schema_version": "1",
        "packet_schema_version": "mbo_feature_packet_v1",
        "packet_id": "mbo_pkt_001",
        "instrument": {
            "instrument_id": "CME_ES_FRONT",
            "symbol": "ESM6",
            "venue": "CME",
            "data_class": "L3_MBO",
            "source": "databento_glbx_mdp3",
        },
        "timestamp_ns": 1_800_000_000_000_000_000,
        "receive_timestamp_ns": 1_800_000_000_000_001_000,
        "latency_budget_us": 750.0,
        "spread_ticks": 1.0,
        "event_rate_10ms": 12.0,
        "event_rate_100ms": 95.0,
        "event_rate_1s": 640.0,
        "depth": {
            "imbalance_1": 0.1,
            "imbalance_5": 0.2,
            "imbalance_10": 0.3,
            "exp_decay_imbalance": 0.18,
            "order_count_imbalance_10": 0.05,
            "age_weighted_imbalance_10": 0.08,
            "entropy_bid_10": 1.2,
            "entropy_ask_10": 1.1,
            "order_concentration_score": 0.42,
            "wall_quality_score_bid": 0.7,
            "wall_quality_score_ask": 0.55,
            "cumulative_depth_slope": -0.02,
            "depth_curvature": 0.01,
            "depth_convexity": -0.01,
            "book_shape_distance": 0.4,
            "wasserstein_regime_distance": 0.22,
        },
        "microprice": {
            "level_1_ticks": 0.12,
            "multi_level_ticks": 0.08,
            "drift_vs_mid_ticks": 0.03,
        },
        "flow": {
            "ofi_10ms": 4.0,
            "ofi_100ms": 18.0,
            "ofi_1s": 45.0,
            "normalized_ofi_10ms": 0.2,
            "normalized_ofi_100ms": 0.18,
            "normalized_ofi_1s": 0.12,
            "mlofi_level_count": 5,
            "mlofi_unit": "contracts",
            "mlofi_side_convention": "bid_add_or_sell_cancel_positive",
            "mlofi_vector": [4.0, 8.0, 9.0, 10.0, 14.0],
            "cancel_imbalance": -0.2,
            "trade_imbalance": 0.15,
            "add_imbalance": 0.05,
            "imbalance_velocity": 0.7,
            "imbalance_acceleration": 0.2,
            "cancel_acceleration": -0.1,
        },
        "queue": {
            "bid_depletion_probability": 0.2,
            "ask_depletion_probability": 0.35,
            "volume_ahead_bid": 42.0,
            "volume_ahead_ask": 31.0,
            "volume_behind_bid": 18.0,
            "volume_behind_ask": 14.0,
            "queue_rank_bid": 3,
            "queue_rank_ask": 4,
            "queue_front_imbalance": 0.1,
            "expected_fill_time_bid": 0.04,
            "expected_fill_time_ask": 0.05,
            "passive_fill_probability": 0.3,
            "adverse_selection_probability": 0.25,
        },
        "liquidity_quality": {
            "bid_absorption_score": 1.4,
            "ask_absorption_score": 0.7,
            "iceberg_score_bid": 0.3,
            "iceberg_score_ask": 0.1,
            "replenishment_score_bid": 0.6,
            "replenishment_score_ask": 0.45,
            "displayed_depth_resistance_bid": 0.52,
            "displayed_depth_resistance_ask": 0.41,
            "fleeting_liquidity_bid": 0.2,
            "fleeting_liquidity_ask": 0.4,
            "fragile_wall_score_bid": 0.25,
            "fragile_wall_score_ask": 0.35,
            "vacuum_score_bid": 0.1,
            "vacuum_score_ask": 0.2,
        },
        "event_model": {
            "hawkes_buy_trade_intensity": 5.0,
            "hawkes_sell_trade_intensity": 4.0,
            "hawkes_cancel_bid_intensity": 7.0,
            "hawkes_cancel_ask_intensity": 9.0,
            "queue_reactive_up_probability": 0.55,
            "queue_reactive_down_probability": 0.45,
        },
        "cross_asset": {
            "leading_ofi_refs": [
                {
                    "instrument_id": "CME_NQ_FRONT",
                    "lag_ms": -12,
                    "ofi": 0.5,
                    "confidence": 0.7,
                }
            ],
            "cross_impact_score": 0.3,
            "lead_lag_confidence": 0.6,
        },
        "execution": {
            "raw_edge_bps": 0.7,
            "latency_adjusted_edge_bps": 0.4,
            "post_ev": 0.2,
            "take_ev": -0.1,
            "cancel_ev": 0.0,
            "authority": "advisory_only",
            "advisory_signal": "hold",
            "confidence": 0.62,
        },
        "audit": {
            "point_in_time_safe": True,
            "no_execution_authority": True,
            "source_doc_ids": ["docs/research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md"],
            "feature_family_codes": [
                "static_depth",
                "microprice",
                "dynamic_ofi",
                "queue_position",
                "age_survival",
                "cancellation",
                "replenishment",
                "iceberg",
                "fleeting_liquidity",
                "entropy",
                "book_shape_geometry",
                "hawkes_intensity",
                "queue_reactive",
                "cross_asset_ofi",
                "adverse_selection",
                "latency_haircut",
            ],
        },
    }


def test_mbo_feature_packet_schema_is_closed_and_valid():
    schema = _load_schema()
    jsonschema.Draft7Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert "receive_timestamp_ns" in schema["required"]
    assert "microprice" in schema["required"]
    assert "recommended_action" not in schema["properties"]["execution"]["properties"]


def test_mbo_feature_packet_valid_minimal():
    assert validate_mbo_feature_packet(_sample_packet()) == []


def test_mbo_feature_packet_requires_l3_mbo_data_class():
    sample = _sample_packet()
    sample["instrument"]["data_class"] = "TRADES_ONLY"
    errors = validate_mbo_feature_packet(sample)
    assert any("data_class" in err and "L3_MBO" in err for err in errors)


def test_mbo_feature_packet_rejects_narrative_fields():
    sample = _sample_packet()
    sample["narrative_md"] = "not machine packet content"
    errors = validate_mbo_feature_packet(sample)
    assert any("Additional properties" in err and "narrative_md" in err for err in errors)


def test_mbo_feature_packet_rejects_execution_routing_fields():
    sample = deepcopy(_sample_packet())
    sample["execution"]["order_route"] = "paper"
    errors = validate_mbo_feature_packet(sample)
    assert any("Additional properties" in err and "order_route" in err for err in errors)


def test_mbo_feature_packet_rejects_execution_verbs_as_advisory_signal():
    sample = deepcopy(_sample_packet())
    sample["execution"]["advisory_signal"] = "post_bid"
    errors = validate_mbo_feature_packet(sample)
    assert any("advisory_signal" in err and "post_bid" in err for err in errors)


def test_mbo_feature_packet_rejects_legacy_recommended_action_field():
    sample = deepcopy(_sample_packet())
    sample["execution"]["recommended_action"] = "post_bid"
    errors = validate_mbo_feature_packet(sample)
    assert any("Additional properties" in err and "recommended_action" in err for err in errors)


def test_mbo_feature_packet_rejects_bad_probability():
    sample = _sample_packet()
    sample["queue"]["ask_depletion_probability"] = 1.5
    errors = validate_mbo_feature_packet(sample)
    assert any("ask_depletion_probability" in err and "greater than" in err for err in errors)


def test_mbo_feature_packet_rejects_impossible_normalized_ratio():
    sample = _sample_packet()
    sample["depth"]["imbalance_10"] = 2.0
    errors = validate_mbo_feature_packet(sample)
    assert any("imbalance_10" in err and "greater than" in err for err in errors)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda sample: sample.__setitem__("spread_ticks", float("nan")), "spread_ticks"),
        (
            lambda sample: sample["queue"].__setitem__(
                "ask_depletion_probability", float("nan")
            ),
            "queue.ask_depletion_probability",
        ),
        (
            lambda sample: sample["execution"].__setitem__(
                "raw_edge_bps", float("inf")
            ),
            "execution.raw_edge_bps",
        ),
        (
            lambda sample: sample["flow"]["mlofi_vector"].__setitem__(
                0, float("-inf")
            ),
            "flow.mlofi_vector[0]",
        ),
    ],
)
def test_mbo_feature_packet_rejects_non_finite_numbers(mutate, path):
    sample = _sample_packet()
    mutate(sample)
    errors = validate_mbo_feature_packet(sample)
    assert any(path in err and "finite" in err for err in errors)


def test_mbo_feature_packet_rejects_receive_before_event_time():
    sample = _sample_packet()
    sample["receive_timestamp_ns"] = sample["timestamp_ns"] - 1
    errors = validate_mbo_feature_packet(sample)
    assert "receive_timestamp_ns must be >= timestamp_ns" in errors


def test_mbo_feature_packet_rejects_latency_budget_breach():
    sample = _sample_packet()
    sample["latency_budget_us"] = 0.5
    sample["receive_timestamp_ns"] = sample["timestamp_ns"] + 1_000
    errors = validate_mbo_feature_packet(sample)
    assert "receive_timestamp_ns - timestamp_ns exceeds latency_budget_us" in errors


def test_mbo_feature_packet_rejects_mlofi_level_count_mismatch():
    sample = _sample_packet()
    sample["flow"]["mlofi_level_count"] = 4
    errors = validate_mbo_feature_packet(sample)
    assert "flow.mlofi_level_count must equal len(flow.mlofi_vector)" in errors


def test_mbo_feature_packet_requires_canonical_source_doc_id():
    sample = _sample_packet()
    sample["audit"]["source_doc_ids"] = ["docs/research/OTHER.md"]
    errors = validate_mbo_feature_packet(sample)
    assert any("MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md" in err for err in errors)


def test_mbo_feature_packet_requires_complete_feature_family_set():
    sample = _sample_packet()
    sample["audit"]["feature_family_codes"].remove("microprice")
    errors = validate_mbo_feature_packet(sample)
    assert any("missing required MBO families" in err and "microprice" in err for err in errors)


def test_mbo_feature_packet_rejects_hardcoded_cross_asset_fields():
    sample = _sample_packet()
    sample["cross_asset"]["leading_ofi_es"] = 1.0
    errors = validate_mbo_feature_packet(sample)
    assert any("Additional properties" in err and "leading_ofi_es" in err for err in errors)


def test_source_doc_packet_block_matches_machine_contract_names():
    text = (REPO / "docs" / "research" / "MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md").read_text(
        encoding="utf-8"
    )
    required_names = [
        "microprice:",
        "level_1_ticks:",
        "normalized_ofi_10ms:",
        "mlofi_level_count:",
        "volume_ahead_bid:",
        "cancel_acceleration:",
        "wasserstein_regime_distance:",
        "leading_ofi_refs:",
        "authority: \"advisory_only\"",
        "advisory_signal:",
    ]
    for name in required_names:
        assert name in text
    assert "recommended_action:" not in text
    assert "leading_ofi_es:" not in text
