"""Tests for packet_runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "workbench_run_minimal"

pytest.importorskip("jsonschema")

from data_layer.llm import ollama_client  # noqa: E402
from data_layer.llm.packet_runner import run_llm_on_aar_packet  # noqa: E402
from data_layer.packet.microstructure_aar_packet import build_microstructure_aar_packet  # noqa: E402
from data_layer.symbolic.latency_invariants import check_latency_invariants  # noqa: E402


def _mock_response(run_id: str = "minimal") -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "run_id": run_id,
            "input_schema_version": "1",
            "llm_model": "mock",
            "llm_elapsed_s": 0.2,
            "llm_status": "ok",
            "symbolic_passed": True,
            "decision": {"promote_candidate_recommendation": False},
            "kg_annotations": [],
            "narrative_md": "# After-action\n\nMock OK.",
        }
    )


def test_run_llm_on_aar_packet_mock_ok():
    packet, skip_reasons = build_microstructure_aar_packet(FIXTURE, REPO)
    symbolic = check_latency_invariants(packet)
    assert symbolic["passed"] is True

    with patch.object(ollama_client, "ollama_available", return_value=True), patch.object(
        ollama_client,
        "generate",
        return_value=ollama_client.GenerateResult(_mock_response(packet["run_id"]), model="mock", elapsed_s=0.2),
    ):
        out = run_llm_on_aar_packet(
            packet,
            symbolic,
            repo_root=REPO,
            skip_reasons=skip_reasons,
        )

    assert out["llm_status"] == "ok"
    assert out["narrative_md"].startswith("# After-action")


def test_run_llm_skips_on_symbolic_fail():
    packet, skip_reasons = build_microstructure_aar_packet(FIXTURE, REPO)
    symbolic = {"passed": False, "violations": ["test violation"]}

    out = run_llm_on_aar_packet(packet, symbolic, repo_root=REPO, skip_reasons=skip_reasons)

    assert out["llm_status"] == "skipped_symbolic"
    assert "symbolic-only" in out["narrative_md"].lower() or "Symbolic" in out["narrative_md"]


def test_run_llm_schema_reject_on_bad_json():
    packet, skip_reasons = build_microstructure_aar_packet(FIXTURE, REPO)
    symbolic = check_latency_invariants(packet)

    with patch.object(ollama_client, "ollama_available", return_value=True), patch.object(
        ollama_client,
        "generate",
        return_value=ollama_client.GenerateResult('{"not": "schema"}', model="mock", elapsed_s=0.1),
    ):
        out = run_llm_on_aar_packet(packet, symbolic, repo_root=REPO, skip_reasons=skip_reasons)

    assert out["llm_status"] == "schema_reject"
    from data_layer.packet.validate import validate_aar_packet_out

    assert validate_aar_packet_out(out) == []


def test_skip_paths_emit_valid_response():
    from data_layer.packet.validate import validate_aar_packet_out

    packet, skip_reasons = build_microstructure_aar_packet(FIXTURE, REPO)
    symbolic = check_latency_invariants(packet)
    out = run_llm_on_aar_packet(
        packet,
        symbolic,
        repo_root=REPO,
        skip_reasons=skip_reasons + ["HISTORY_GATE"],
    )
    assert out["llm_status"] == "skipped_history_gate"
    assert validate_aar_packet_out(out) == []


def test_promote_clamp_when_lane_pass_false():
    from data_layer.llm.packet_runner import _clamp_promote_recommendation

    packet, _ = build_microstructure_aar_packet(FIXTURE, REPO)
    packet["latency_authority"]["promote_candidate"] = True
    packet["latency_authority"]["lane_pass"] = False
    symbolic = {"passed": True, "violations": []}
    parsed = {
        "schema_version": "1",
        "run_id": packet["run_id"],
        "input_schema_version": "1",
        "llm_model": "mock",
        "llm_elapsed_s": 0.1,
        "llm_status": "ok",
        "symbolic_passed": True,
        "decision": {"promote_candidate_recommendation": True},
        "kg_annotations": [],
        "narrative_md": "x",
    }
    _clamp_promote_recommendation(parsed, packet, symbolic)
    assert parsed["decision"]["promote_candidate_recommendation"] is False
