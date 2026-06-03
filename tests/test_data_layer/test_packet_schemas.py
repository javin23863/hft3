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


def test_aar_packet_in_fixture():
    fixture = REPO / "tests" / "fixtures" / "workbench_run_minimal"
    from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet

    packet, _ = build_microstructure_aar_packet(fixture, REPO)
    errors = validate_aar_packet_in(packet)
    assert errors == [], errors
