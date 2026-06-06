"""Schema tests for MBO LLM agent ontology hardening."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from data_layer.packet.agent_contracts import validate_agent_contract  # noqa: E402
from data_layer.packet.validate import validate_mbo_feature_packet  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO / "docs" / "schemas"
RUNTIME_SCHEMA = REPO / "packages" / "data_layer" / "packet" / "schema_mbo_feature_packet_v1.json"
FIXTURE_PATH = REPO / "tests" / "fixtures" / "mbo_minimal_replay_fixture.json"

SCHEMA_NAMES = [
    "MBO_FEATURE_PACKET.schema.json",
    "HYPOTHESIS_CARD.schema.json",
    "FEATURE_CARD.schema.json",
    "MODEL_CARD.schema.json",
    "VALIDATION_CARD.schema.json",
    "AGENT_INTERPRETATION.schema.json",
]


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validator(name: str) -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(
        _load_schema(name),
        format_checker=jsonschema.FormatChecker(),
    )


def _assert_valid(name: str, payload: dict) -> None:
    errors = validate_agent_contract(name, payload)
    assert errors == []


def _assert_invalid(name: str, payload: dict, expected: str) -> None:
    errors = validate_agent_contract(name, payload)
    assert any(expected in message for message in errors), errors


@pytest.fixture()
def mbo_packet() -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return fixture["expected_packet"]


def test_required_agent_schema_files_exist_and_parse():
    for name in SCHEMA_NAMES:
        schema = _load_schema(name)
        jsonschema.Draft7Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_mbo_feature_packet_fixture_is_valid(mbo_packet: dict):
    _assert_valid("MBO_FEATURE_PACKET.schema.json", mbo_packet)
    assert validate_mbo_feature_packet(mbo_packet) == []


def test_docs_mbo_feature_packet_schema_mirrors_runtime_schema():
    docs_schema = _load_schema("MBO_FEATURE_PACKET.schema.json")
    runtime_schema = json.loads(RUNTIME_SCHEMA.read_text(encoding="utf-8"))
    assert docs_schema == runtime_schema


def test_mbo_feature_packet_required_contract_fields_exist():
    schema = _load_schema("MBO_FEATURE_PACKET.schema.json")
    for field in [
        "packet_schema_version",
        "packet_id",
        "instrument",
        "timestamp_ns",
        "receive_timestamp_ns",
        "latency_budget_us",
        "spread_ticks",
        "event_rate_10ms",
        "event_rate_100ms",
        "event_rate_1s",
        "depth",
        "microprice",
        "flow",
        "queue",
        "liquidity_quality",
        "event_model",
        "cross_asset",
        "execution",
        "audit",
    ]:
        assert field in schema["required"]


def test_packet_requires_source_lineage_and_timestamp_policy(mbo_packet: dict):
    assert mbo_packet["instrument"]["source"]
    assert mbo_packet["timestamp_ns"] <= mbo_packet["receive_timestamp_ns"]
    assert mbo_packet["audit"]["point_in_time_safe"] is True
    assert mbo_packet["audit"]["no_execution_authority"] is True
    assert "docs/research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md" in mbo_packet["audit"]["source_doc_ids"]


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("queue", "ask_depletion_probability"),
        ("queue", "passive_fill_probability"),
        ("liquidity_quality", "fleeting_liquidity_ask"),
        ("event_model", "queue_reactive_up_probability"),
        ("execution", "confidence"),
    ],
)
def test_probability_fields_are_bounded(mbo_packet: dict, section: str, field: str):
    bad = copy.deepcopy(mbo_packet)
    bad[section][field] = 1.5
    _assert_invalid("MBO_FEATURE_PACKET.schema.json", bad, "greater than the maximum")


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("depth", "imbalance_1"),
        ("depth", "exp_decay_imbalance"),
        ("flow", "normalized_ofi_10ms"),
        ("flow", "cancel_imbalance"),
        ("queue", "queue_front_imbalance"),
    ],
)
def test_imbalance_fields_are_bounded(mbo_packet: dict, section: str, field: str):
    bad = copy.deepcopy(mbo_packet)
    bad[section][field] = -1.5
    _assert_invalid("MBO_FEATURE_PACKET.schema.json", bad, "less than the minimum")


def test_null_fields_are_allowed_only_where_schema_permits(mbo_packet: dict):
    bad = copy.deepcopy(mbo_packet)
    bad["spread_ticks"] = None
    _assert_invalid("MBO_FEATURE_PACKET.schema.json", bad, "None is not of type")

    model = _sample_model_card()
    model["model_card"]["performance_metrics"]["sharpe"] = None
    _assert_valid("MODEL_CARD.schema.json", model)


def test_mbo_feature_packet_rejects_non_finite_and_unsafe_flags(mbo_packet: dict):
    bad = copy.deepcopy(mbo_packet)
    bad["queue"]["ask_depletion_probability"] = float("nan")
    _assert_invalid("MBO_FEATURE_PACKET.schema.json", bad, "must be finite")

    bad = copy.deepcopy(mbo_packet)
    bad["audit"]["point_in_time_safe"] = False
    _assert_invalid("MBO_FEATURE_PACKET.schema.json", bad, "True was expected")

    bad = copy.deepcopy(mbo_packet)
    bad["receive_timestamp_ns"] = bad["timestamp_ns"] - 1
    assert "receive_timestamp_ns must be >= timestamp_ns" in validate_mbo_feature_packet(bad)


def test_minimal_replay_fixture_covers_required_event_shapes():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    actions = [event["action"] for event in fixture["events"]]
    assert actions[:3] == ["add", "add", "add"]
    assert "cancel" in actions
    assert "partial_fill" in actions
    assert actions.count("full_fill") == 3
    assert fixture["expected_assertions"]["contains_ask_depletion"] is True
    assert fixture["expected_assertions"]["contains_bid_depletion"] is True
    assert fixture["expected_assertions"]["same_level_replenishment_events"] == 1


def test_hypothesis_card_schema_accepts_structured_idea():
    payload = {
        "hypothesis_card": {
            "hypothesis_id": "mbo_queue_breach_001",
            "title": "Ask queue depletion predicts short-horizon upward mid-price move",
            "ontology_tags": ["queue_breach", "order_flow_imbalance"],
            "mechanism": "Ask liquidity depletes faster than it replenishes.",
            "participant_behavior_assumption": "Aggressive buyers consume offers while providers retreat.",
            "measurable_footprint": "High ask depletion probability and positive normalized OFI.",
            "required_data": [
                {
                    "dataset_id": "mbo_minimal_replay_v1",
                    "source_tier": "tier_0_reality",
                    "required_fields": ["order_id", "side", "price", "size", "action"],
                }
            ],
            "required_features": ["ask_depletion_probability", "normalized_ofi_10"],
            "target_label": "future_mid_move_100ms",
            "prediction_horizon": "100ms",
            "expected_direction": "up",
            "falsification_tests": ["No lift over baseline imbalance."],
            "leakage_risks": ["Using events after decision timestamp."],
            "execution_dependencies": ["Latency below signal half-life."],
            "regime_scope": ["directional", "fragile"],
            "asset_scope": ["future"],
            "minimum_evidence_threshold": "Positive lift after latency haircut.",
            "rejection_conditions": ["Fails out of sample."],
        }
    }
    _assert_valid("HYPOTHESIS_CARD.schema.json", payload)


def test_feature_model_and_validation_cards_parse_valid_samples():
    _assert_valid(
        "FEATURE_CARD.schema.json",
        {
            "feature_card": {
                "feature_id": "normalized_ofi_10",
                "feature_family": "flow",
                "formula": "(bid_add + ask_cancel - ask_add - bid_cancel) / total_events",
                "plain_english_definition": "Normalized short-window order flow pressure.",
                "required_inputs": ["MBOEvent.action", "MBOEvent.side"],
                "window_type": "clock_time",
                "lookback_window": "10ms",
                "timestamp_policy": "Only events with receive_timestamp <= decision_timestamp.",
                "dataset_id": "mbo_minimal_replay_v1",
                "source_tier_required": "tier_0_reality",
                "leakage_risk": "low",
                "expected_range": "[-1, 1]",
                "normalization": "Divide by total eligible events.",
                "missing_value_policy": "Emit 0 when no eligible events exist.",
                "validation_tests": ["point_in_time_replay", "bounds_check"],
                "failure_modes": ["timestamp drift", "missing order IDs"],
            }
        },
    )
    _assert_valid("MODEL_CARD.schema.json", _sample_model_card())
    _assert_valid(
        "VALIDATION_CARD.schema.json",
        {
            "validation_card": {
                "validation_id": "mbo_validation_001",
                "object_type": "hypothesis",
                "object_id": "mbo_queue_breach_001",
                "point_in_time_safe": True,
                "leakage_tests_passed": True,
                "survivorship_bias_checked": True,
                "timestamp_alignment_checked": True,
                "deterministic_replay_passed": True,
                "baseline_comparison_passed": False,
                "robustness_tests_passed": False,
                "execution_friction_checked": True,
                "latency_haircut_checked": True,
                "regime_coverage_checked": False,
                "cross_symbol_checked": False,
                "failure_reasons": ["not enough evidence yet"],
                "required_remediation": ["run robustness pipeline"],
            }
        },
    )


def test_agent_interpretation_requires_structured_sections():
    payload = {
        "agent_interpretation": {
            "packet_id": "mbo_pkt_001",
            "instrument": "ESM6",
            "timestamp": "2026-06-05T14:30:00.000108Z",
            "observed_state": {
                "dominant_behavior": "queue_breach",
                "evidence_features": ["ask_depletion_probability"],
                "source_tier": "tier_0_reality",
            },
            "mechanism_assessment": {
                "likely_mechanism": "Ask queue depletion after same-level replenishment.",
                "confidence": 0.62,
                "alternative_explanations": ["fixture artifact"],
            },
            "execution_assessment": {
                "signal_half_life_estimate": "unknown",
                "latency_survival": "unknown",
                "fill_quality": "unknown",
                "adverse_selection_risk": "medium",
            },
            "research_action": {
                "recommended_action": "create_hypothesis_card",
                "reason": "Structured evidence exists but requires falsification.",
            },
            "prohibited_claim_check": {
                "used_future_data": False,
                "invented_source": False,
                "narrative_only": False,
                "unsupported_causality": False,
            },
        }
    }
    _assert_valid("AGENT_INTERPRETATION.schema.json", payload)

    narrative_only = {"summary": "Looks bullish because the book feels weak."}
    _assert_invalid("AGENT_INTERPRETATION.schema.json", narrative_only, "agent_interpretation")

    bad = copy.deepcopy(payload)
    bad["agent_interpretation"]["timestamp"] = "not-a-date"
    _assert_invalid("AGENT_INTERPRETATION.schema.json", bad, "must be date-time")

    for flag in ("used_future_data", "invented_source", "unsupported_causality"):
        bad = copy.deepcopy(payload)
        bad["agent_interpretation"]["prohibited_claim_check"][flag] = True
        _assert_invalid("AGENT_INTERPRETATION.schema.json", bad, "False was expected")

    bad = copy.deepcopy(payload)
    bad["agent_interpretation"]["mechanism_assessment"]["confidence"] = float("nan")
    _assert_invalid("AGENT_INTERPRETATION.schema.json", bad, "must be finite")


def test_model_card_promotion_requires_passing_validation_status():
    model = _sample_model_card()
    model["model_card"]["promotion_status"] = "production_eligible"
    model["model_card"]["validation_status"]["robustness_tests_passed"] = False
    _assert_invalid("MODEL_CARD.schema.json", model, "True was expected")

    model = _sample_model_card()
    model["model_card"]["baseline_comparison"]["uplift_value"] = float("inf")
    _assert_invalid("MODEL_CARD.schema.json", model, "must be finite")

    model = _sample_model_card()
    model["model_card"]["promotion_status"] = "robustness_passed"
    model["model_card"]["validation_status"] = {
        "point_in_time_safe": True,
        "leakage_tests_passed": True,
        "robustness_tests_passed": True,
        "execution_friction_checked": True,
        "latency_haircut_checked": True,
    }
    _assert_valid("MODEL_CARD.schema.json", model)


def test_model_card_feature_ids_must_resolve_to_registry_features():
    model = _sample_model_card()
    model["model_card"]["feature_ids"] = ["not_registered_feature"]
    _assert_invalid("MODEL_CARD.schema.json", model, "unknown feature_id not_registered_feature")

    model = _sample_model_card()
    model["model_card"]["feature_ids"] = ["BOOK_PRESSURE"]
    _assert_invalid("MODEL_CARD.schema.json", model, "unknown feature_id BOOK_PRESSURE")

    model = _sample_model_card()
    model["model_card"]["model_id"] = "BOOK_PRESSURE"
    model["model_card"]["model_type"] = "pdf_structural"
    model["model_card"]["feature_ids"] = ["crypto.basis.spot_perp_basis"]
    _assert_invalid("MODEL_CARD.schema.json", model, "not eligible for model_kind pdf_structural")


def test_llm_facing_markdown_files_have_required_front_matter():
    docs = [
        REPO / "docs" / "agents" / "MBO_AGENT_ONTOLOGY_HARDENING_SOURCE_OF_TRUTH.md",
        REPO / "docs" / "agents" / "MBO_AGENT_OPERATING_DOCTRINE.md",
        REPO / "docs" / "agents" / "MBO_RESEARCH_AGENT_SPEC.md",
        REPO / "docs" / "agents" / "MBO_MODEL_DEVELOPMENT_AGENT_SPEC.md",
        REPO / "docs" / "ontology" / "MBO_MARKET_ONTOLOGY.md",
        REPO / "docs" / "ontology" / "SOURCE_OF_TRUTH_POLICY.md",
    ]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        for key in [
            "document_type:",
            "agent_scope:",
            "schema_version:",
            'owner: "HFT3"',
            "allowed_domains:",
            "prohibited_domains:",
            "last_updated:",
        ]:
            assert key in text, path


def _sample_model_card() -> dict:
    return {
        "model_card": {
            "model_id": "mbo_queue_breach_model_001",
            "model_type": "existing_hft3_candidate",
            "hypothesis_ids": ["mbo_queue_breach_001"],
            "feature_ids": ["normalized_ofi_10"],
            "label_id": "future_mid_move_100ms",
            "asset_scope": ["future"],
            "regime_scope": ["directional"],
            "training_window": "2024-01-01/2024-12-31",
            "validation_window": "2025-01-01/2025-06-30",
            "holdout_window": "2025-07-01/2025-12-31",
            "robustness_tests": ["walk_forward", "latency_haircut"],
            "validation_card_id": "mbo_validation_001",
            "validation_status": {
                "point_in_time_safe": True,
                "leakage_tests_passed": True,
                "robustness_tests_passed": False,
                "execution_friction_checked": True,
                "latency_haircut_checked": True,
            },
            "baseline_comparison": {
                "baseline_model": "depth_imbalance_baseline",
                "uplift_metric": "auc",
                "uplift_value": 0.02,
            },
            "performance_metrics": {
                "sharpe": None,
                "sortino": None,
                "max_drawdown": None,
                "hit_rate": None,
                "profit_factor": None,
                "expectancy": None,
                "turnover": None,
                "capacity_proxy": None,
            },
            "microstructure_metrics": {
                "fill_probability": None,
                "adverse_selection_probability": None,
                "latency_adjusted_edge_bps": None,
                "queue_survival_score": None,
            },
            "known_failure_modes": ["fragile regime overfit"],
            "rejection_conditions": ["fails latency haircut"],
            "promotion_status": "research_only",
            "explanation_packet": "validation_card:mbo_validation_001",
        }
    }
