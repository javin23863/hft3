"""Tests for packet JSON schemas."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKET_DIR = REPO / "packages" / "data_layer" / "packet"

jsonschema = pytest.importorskip("jsonschema")

from data_layer.packet.validate import (  # noqa: E402
    validate_aar_packet_in,
    validate_aar_packet_out,
    validate_pipeline_idea_set,
    validate_pipeline_request,
    validate_pipeline_response,
    validate_research_decision_packet,
)


def _load(name: str) -> dict:
    return json.loads((PACKET_DIR / name).read_text(encoding="utf-8"))


def _minimal_research_decision_packet() -> dict:
    return {
        "packet_schema_version": "research_decision_packet_v1",
        "packet_id": "rdp_001",
        "created_at_utc": "2026-06-04T13:43:02Z",
        "decision_context": {
            "mode": "research",
            "allowed_actions": [
                "generate_hypothesis",
                "write_validation_code",
                "interpret_sandbox_result",
                "reject_hypothesis",
            ],
            "forbidden_actions": [
                "invent_variable",
                "invent_formula",
                "infer_trade_direction_from_news_only",
                "skip_validation",
                "modify_hypothesis_after_failure_without_audit",
                "promote_without_validation",
            ],
            "no_promotion_authority": True,
        },
        "market_state": {
            "market_state_id": "mkt_001",
            "as_of_utc": "2026-06-04T13:42:55Z",
            "lookback_window": "5m",
            "instruments": [
                {
                    "symbol": "MES",
                    "canonical_instrument_id": "CME_MES_FRONT",
                    "asset_class": "future",
                    "venue": "CME",
                    "session_state": "open",
                    "mid_price": 5300.25,
                    "spread": 0.25,
                    "spread_bps": 0.47,
                    "top_of_book_imbalance": 0.1,
                    "depth_imbalance_10": 0.05,
                    "order_flow_imbalance": 0.02,
                    "microprice": 5300.27,
                    "realized_volatility": 0.12,
                    "book_update_rate": 10.0,
                    "trade_rate": 2.0,
                    "cancel_rate": 3.0,
                    "toxicity_metric": None,
                    "liquidity_regime": "normal",
                    "volatility_regime": "normal",
                    "data_quality_status": "passed",
                }
            ],
            "cross_asset_features": {
                "basis_spreads": [],
                "rolling_correlations": [],
                "cointegration_residuals": [],
                "lead_lag_candidates": [],
                "funding_rate_features": [],
                "macro_alignment_features": [],
            },
            "data_validation": {
                "status": "passed",
                "failed_checks": [],
                "warnings": [],
            },
        },
        "event_state": {
            "active_events": [],
            "event_clusters": [],
            "ignored_events": [],
            "contradictory_events": [],
        },
        "knowledge_state": {
            "approved_sources_retrieved": [
                {
                    "source_id": "src_microstructure_pdf",
                    "source_type": "approved_pdf",
                    "source_registry_key": "microstructure_pdf_v1",
                    "title": "Chicago CME Microstructure Mathematical Model",
                    "version": "1",
                    "citation": "section 4",
                }
            ],
            "formulas_available": [
                {
                    "formula_id": "top_of_book_imbalance_v1",
                    "name": "Top of book imbalance",
                    "expression": "(bid_size - ask_size) / (bid_size + ask_size)",
                    "variable_ids": ["top_of_book_imbalance"],
                    "source_ids": ["src_microstructure_pdf"],
                }
            ],
            "concepts_available": [
                {
                    "concept_id": "liquidity_shift",
                    "name": "Liquidity shift",
                    "source_ids": ["src_microstructure_pdf"],
                }
            ],
            "source_gaps": [],
        },
        "ontology_state": {
            "allowed_entities": [
                {
                    "entity_id": "CME_MES_FRONT",
                    "entity_name": "Micro E-mini S&P 500 front contract",
                    "entity_type": "future",
                    "source_ids": ["src_microstructure_pdf"],
                }
            ],
            "allowed_variables": [
                {
                    "variable_id": "top_of_book_imbalance",
                    "name": "Top of book imbalance",
                    "data_type": "number",
                    "unit": None,
                    "source_ids": ["src_microstructure_pdf"],
                }
            ],
            "allowed_formulas": [
                {
                    "formula_id": "top_of_book_imbalance_v1",
                    "name": "Top of book imbalance",
                    "expression": "(bid_size - ask_size) / (bid_size + ask_size)",
                    "variable_ids": ["top_of_book_imbalance"],
                    "source_ids": ["src_microstructure_pdf"],
                }
            ],
            "allowed_transformations": [],
            "forbidden_variables": [],
        },
        "candidate_research_questions": [
            {
                "question_id": "rq_001",
                "question": "Did top-of-book imbalance become predictive after the event window?",
                "trigger_source": "market_state",
                "required_variables": ["top_of_book_imbalance"],
                "required_sources": ["src_microstructure_pdf"],
                "required_formulas": ["top_of_book_imbalance_v1"],
                "required_entities": ["CME_MES_FRONT"],
                "testability_status": "testable",
                "rejection_reason": None,
            }
        ],
        "validation_requirements": {
            "required_tests": [
                "source_traceability",
                "ontology_membership",
                "math_consistency",
                "data_quality",
                "point_in_time_safety",
                "walk_forward",
                "out_of_sample",
                "multiple_testing_correction",
                "regime_break",
                "liquidity_conditioning",
            ],
            "minimum_sample_size": 100,
            "walk_forward_required": True,
            "out_of_sample_required": True,
            "multiple_testing_correction_required": True,
            "regime_break_test_required": True,
            "liquidity_conditioning_required": True,
        },
        "risk_handoff_requirements": {
            "must_emit_typed_logic": True,
            "must_emit_failure_conditions": True,
            "must_emit_regime_sensitivity": True,
            "must_emit_liquidity_sensitivity": True,
            "must_emit_latency_sensitivity": True,
        },
        "audit": {
            "data_snapshot_ids": ["snapshot_001"],
            "source_registry_version": "registry_v1",
            "ontology_version": "ontology_v1",
            "code_commit": "abc123",
        },
    }


def test_research_decision_packet_valid_minimal():
    assert validate_research_decision_packet(_minimal_research_decision_packet()) == []


def test_research_decision_packet_rejects_extra_fields():
    sample = _minimal_research_decision_packet()
    sample["unexpected"] = "not allowed"
    errors = validate_research_decision_packet(sample)
    assert any("Additional properties" in err and "unexpected" in err for err in errors)


def test_research_decision_packet_rejects_missing_required_top_level_key():
    sample = _minimal_research_decision_packet()
    del sample["market_state"]
    errors = validate_research_decision_packet(sample)
    assert any("'market_state' is a required property" in err for err in errors)


def test_research_decision_packet_requires_no_promotion_authority_true():
    sample = _minimal_research_decision_packet()
    sample["decision_context"]["no_promotion_authority"] = False
    errors = validate_research_decision_packet(sample)
    assert any("no_promotion_authority" in err and "True was expected" in err for err in errors)


def test_research_decision_packet_rejects_unknown_action():
    sample = _minimal_research_decision_packet()
    sample["decision_context"]["allowed_actions"].append("submit_order")
    errors = validate_research_decision_packet(sample)
    assert any("allowed_actions" in err and "submit_order" in err for err in errors)


def test_research_decision_packet_rejects_execution_routing_field():
    sample = _minimal_research_decision_packet()
    sample["execution_routing"] = {"target": "live"}
    errors = validate_research_decision_packet(sample)
    assert any("Additional properties" in err and "execution_routing" in err for err in errors)


def test_research_decision_packet_questions_require_sources_and_variables():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["candidate_research_questions"][0]["required_sources"] = []
    sample["candidate_research_questions"][0]["required_variables"] = []
    errors = validate_research_decision_packet(sample)
    assert any("required_sources" in err and "should be non-empty" in err for err in errors)
    assert any("required_variables" in err and "should be non-empty" in err for err in errors)


def test_research_decision_packet_rejects_unknown_required_ids():
    sample = deepcopy(_minimal_research_decision_packet())
    question = sample["candidate_research_questions"][0]
    question["required_variables"] = ["unknown_variable"]
    question["required_formulas"] = ["unknown_formula"]
    question["required_entities"] = ["UNKNOWN_ENTITY"]
    question["required_sources"] = ["unknown_source"]

    errors = validate_research_decision_packet(sample)

    assert any("required_variables" in err and "unknown_variable" in err for err in errors)
    assert any("ontology_state.allowed_formulas" in err and "unknown_formula" in err for err in errors)
    assert any("knowledge_state.formulas_available" in err and "unknown_formula" in err for err in errors)
    assert any("required_entities" in err and "UNKNOWN_ENTITY" in err for err in errors)
    assert any("knowledge_state.approved_sources_retrieved" in err and "unknown_source" in err for err in errors)


def test_research_decision_packet_rejects_object_feature_value():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["market_state"]["cross_asset_features"]["basis_spreads"] = [
        {
            "feature_id": "basis_001",
            "feature_type": "basis_spread",
            "variable_ids": ["top_of_book_imbalance"],
            "formula_ids": ["top_of_book_imbalance_v1"],
            "source_ids": ["src_microstructure_pdf"],
            "value": {"execution_routing": "live"},
            "as_of_utc": "2026-06-04T13:42:55Z",
        }
    ]

    errors = validate_research_decision_packet(sample)

    assert any("basis_spreads.0.value" in err for err in errors)


def test_research_decision_packet_rejects_unknown_cross_asset_feature_refs():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["market_state"]["cross_asset_features"]["basis_spreads"] = [
        {
            "feature_id": "basis_001",
            "feature_type": "basis_spread",
            "variable_ids": ["unknown_feature_variable"],
            "formula_ids": ["unknown_feature_formula"],
            "source_ids": ["unknown_feature_source"],
            "value": 1.25,
            "as_of_utc": "2026-06-04T13:42:55Z",
        }
    ]

    errors = validate_research_decision_packet(sample)

    assert any(
        "market_state.cross_asset_features.basis_spreads[0].variable_ids" in err
        and "unknown_feature_variable" in err
        for err in errors
    )
    assert any(
        "market_state.cross_asset_features.basis_spreads[0].formula_ids" in err
        and "ontology_state.allowed_formulas" in err
        and "unknown_feature_formula" in err
        for err in errors
    )
    assert any(
        "market_state.cross_asset_features.basis_spreads[0].formula_ids" in err
        and "knowledge_state.formulas_available" in err
        and "unknown_feature_formula" in err
        for err in errors
    )
    assert any(
        "market_state.cross_asset_features.basis_spreads[0].source_ids" in err
        and "knowledge_state.approved_sources_retrieved" in err
        and "unknown_feature_source" in err
        for err in errors
    )
    assert any(
        "market_state.cross_asset_features.basis_spreads[0].source_ids" in err
        and "ontology/formula/source refs" in err
        and "unknown_feature_source" in err
        for err in errors
    )


def test_research_decision_packet_rejects_unknown_ontology_and_knowledge_refs():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["ontology_state"]["allowed_entities"][0]["source_ids"] = ["unknown_entity_source"]
    sample["ontology_state"]["allowed_variables"][0]["source_ids"] = ["unknown_variable_source"]
    sample["ontology_state"]["allowed_formulas"][0]["source_ids"] = ["unknown_ontology_formula_source"]
    sample["ontology_state"]["allowed_formulas"][0]["variable_ids"] = ["unknown_ontology_formula_variable"]
    sample["ontology_state"]["allowed_transformations"] = [
        {
            "transformation_id": "xform_001",
            "name": "Unknown variable transformation",
            "input_variable_ids": ["unknown_input_variable"],
            "output_variable_ids": ["unknown_output_variable"],
            "source_ids": ["unknown_transformation_source"],
        }
    ]
    sample["knowledge_state"]["formulas_available"][0]["source_ids"] = ["unknown_knowledge_formula_source"]
    sample["knowledge_state"]["formulas_available"][0]["variable_ids"] = ["unknown_knowledge_formula_variable"]
    sample["knowledge_state"]["concepts_available"][0]["source_ids"] = ["unknown_concept_source"]

    errors = validate_research_decision_packet(sample)

    expected = (
        ("ontology_state.allowed_entities[0].source_ids", "unknown_entity_source"),
        ("ontology_state.allowed_variables[0].source_ids", "unknown_variable_source"),
        ("ontology_state.allowed_formulas[0].source_ids", "unknown_ontology_formula_source"),
        ("ontology_state.allowed_formulas[0].variable_ids", "unknown_ontology_formula_variable"),
        ("ontology_state.allowed_transformations[0].input_variable_ids", "unknown_input_variable"),
        ("ontology_state.allowed_transformations[0].output_variable_ids", "unknown_output_variable"),
        ("ontology_state.allowed_transformations[0].source_ids", "unknown_transformation_source"),
        ("knowledge_state.formulas_available[0].source_ids", "unknown_knowledge_formula_source"),
        ("knowledge_state.formulas_available[0].variable_ids", "unknown_knowledge_formula_variable"),
        ("knowledge_state.concepts_available[0].source_ids", "unknown_concept_source"),
    )
    for path, unknown_id in expected:
        assert any(path in err and unknown_id in err for err in errors), (path, unknown_id, errors)


def test_research_decision_packet_rejects_empty_forbidden_actions():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["decision_context"]["forbidden_actions"] = []
    errors = validate_research_decision_packet(sample)
    assert any("forbidden_actions" in err for err in errors)


def test_research_decision_packet_rejects_empty_required_tests():
    sample = deepcopy(_minimal_research_decision_packet())
    sample["validation_requirements"]["required_tests"] = []
    errors = validate_research_decision_packet(sample)
    assert any("required_tests" in err for err in errors)


def test_aar_response_valid_minimal():
    sample = {
        "schema_version": "1",
        "run_id": "run_x",
        "input_schema_version": "1",
        "llm_model": "mock",
        "llm_elapsed_s": 0.1,
        "llm_status": "ok",
        "symbolic_passed": True,
        "decision": {"promote_candidate_recommendation": False},
        "kg_annotations": [],
        "narrative_md": "# OK",
    }
    assert validate_aar_packet_out(sample) == []


def test_aar_response_rejects_bad_status():
    sample = {
        "schema_version": "1",
        "run_id": "run_x",
        "input_schema_version": "1",
        "llm_model": "mock",
        "llm_elapsed_s": 0.1,
        "llm_status": "not_a_status",
        "symbolic_passed": True,
        "decision": {"promote_candidate_recommendation": False},
        "kg_annotations": [],
        "narrative_md": "# OK",
    }
    assert validate_aar_packet_out(sample)


def test_pipeline_request_valid():
    sample = {
        "schema_version": "1",
        "request_id": "req_1",
        "thesis": "test thesis",
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {
            "connector_id": "hft3-cme-mbo",
            "asset_class": "cme_mbo_microstructure",
            "vendor_shas": {"openfoundry": "abc"},
            "schema_version": "1",
        },
        "max_candidates": 3,
    }
    assert validate_pipeline_request(sample) == []


def test_pipeline_response_valid_minimal():
    sample = {
        "schema_version": "1",
        "request_id": "req_1",
        "run_id": "run_1",
        "event_id": "CPI_2024_09_11_TIGHT",
        "llm_model": "gpt-5.5",
        "llm_status": "ok",
        "parsed": {
            "primary_model_id": "HYP_5",
            "instrument_universe": ["ES"],
            "indicators": ["ofi"],
            "source": "openai_compatible",
        },
        "candidates_tested": 0,
        "results": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    assert validate_pipeline_response(sample) == []


def test_pipeline_response_valid_with_idea_summary():
    sample = {
        "schema_version": "1",
        "request_id": "req_idea",
        "run_id": "run_idea",
        "event_id": "CPI_2024_09_11_TIGHT",
        "llm_model": "gpt-5.5",
        "llm_status": "ok",
        "parsed": {
            "primary_model_id": "HYP_5",
            "instrument_universe": ["ES"],
            "indicators": ["ofi"],
            "source": "idea_set",
        },
        "candidates_tested": 0,
        "results": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
        "idea_summary": {
            "ideas_generated": 2,
            "ideas_static_rejected": 1,
            "ideas_queued_for_test": 1,
            "ideas_tested_fail": 0,
            "ideas_tested_pass": 0,
            "candidates_from_ideas": 1,
        },
    }
    assert validate_pipeline_response(sample) == []


def test_pipeline_idea_set_valid_compact_packet():
    sample = {
        "schema_version": "1",
        "request_id": "req_idea",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {
            "ref_event": {"type": "event", "value": "CPI_2024_09_11_TIGHT"},
            "mem_001": {"type": "artifact", "value": "artifacts/run/after_action_response.json"},
        },
        "constraints": {
            "allowed_model_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 3,
            "no_promotion_authority": True,
        },
        "review_memory": [
            {
                "memory_id": "mem_001",
                "ref_id": "mem_001",
                "fact_codes": ["llm:ok", "symbolic:pass"],
                "metric_values": {"net_pnl": 1.0},
                "authority": "advisory",
            }
        ],
        "ideas": [
            {
                "idea_id": "idea_001",
                "status": "queued_for_test",
                "lane_code": "cme",
                "thesis_code": "spread_recompression",
                "instrument_ids": ["MES"],
                "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "feature_ids": ["mbo.depth.spread_stress"],
                "param_ranges": {"signal_threshold": [0.05, 0.35]},
                "entry_rule_codes": ["enter_spread_signal"],
                "exit_rule_codes": ["exit_revert"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": ["mem_001"],
                "rank_inputs": {
                    "novelty": 0.1,
                    "evidence_coverage": 0.2,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            }
        ],
    }
    assert validate_pipeline_idea_set(sample) == []


def test_pipeline_idea_set_rejects_narrative_field():
    sample = {
        "schema_version": "1",
        "request_id": "req_idea",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {},
        "constraints": {
            "allowed_model_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 3,
            "no_promotion_authority": True,
        },
        "review_memory": [],
        "ideas": [],
        "narrative_md": "not allowed",
    }
    errors = validate_pipeline_idea_set(sample)
    assert any("Additional properties" in err and "narrative_md" in err for err in errors)


def test_pipeline_idea_set_rejects_unknown_idea_status():
    sample = {
        "schema_version": "1",
        "request_id": "req_idea",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {},
        "constraints": {
            "allowed_model_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 3,
            "no_promotion_authority": True,
        },
        "review_memory": [],
        "ideas": [
            {
                "idea_id": "idea_001",
                "status": "ok",
                "lane_code": "cme",
                "thesis_code": "spread_recompression",
                "instrument_ids": ["MES"],
                "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "feature_ids": ["mbo.depth.spread_stress"],
                "param_ranges": {"signal_threshold": [0.05, 0.35]},
                "entry_rule_codes": ["enter_spread_signal"],
                "exit_rule_codes": ["exit_revert"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": [],
                "rank_inputs": {
                    "novelty": 0.1,
                    "evidence_coverage": 0.2,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            }
        ],
    }
    errors = validate_pipeline_idea_set(sample)
    assert any("status" in err and "is not one of" in err for err in errors)


def test_aar_packet_in_fixture():
    fixture = REPO / "tests" / "fixtures" / "workbench_run_minimal"
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    packet, _ = build_microstructure_aar_packet(fixture, REPO)
    errors = validate_aar_packet_in(packet)
    assert errors == [], errors
