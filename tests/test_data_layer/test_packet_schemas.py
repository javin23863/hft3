"""Tests for packet JSON schemas."""

from __future__ import annotations

import json
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
)


def _load(name: str) -> dict:
    return json.loads((PACKET_DIR / name).read_text(encoding="utf-8"))


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
                "feature_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
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
                "feature_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
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
