"""Tests for packages/research_pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "docs" / "references" / "dev_instructions.pdf"


def test_extract_text_dev_instructions_pdf():
    if not PDF.is_file():
        pytest.skip("dev_instructions.pdf not in repo")
    from research_pipeline.document_ingestion import extract_text

    text = extract_text(PDF)
    assert "Hypothesis ingestion" in text or "hypothesis" in text.lower()
    assert len(text) > 200


def test_parse_hypothesis_heuristic_spread():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Fade spread blowout after CPI release on MES", use_llm=False)
    assert parsed.primary_model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert parsed.source == "heuristic"
    assert "MES" in parsed.instrument_universe


def test_generate_candidates_respects_max():
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.model_generation import generate_candidates

    parsed = parse_hypothesis("spread recompression", use_llm=False)
    cands = list(generate_candidates(parsed, max_candidates=2))
    assert len(cands) == 2
    assert cands[0].strategy_params["signal_threshold"] != cands[1].strategy_params["signal_threshold"]


def test_gate_thresholds():
    from research_pipeline.types import GateThresholds

    gates = GateThresholds(min_net_pnl=0.0, min_trades=1)
    assert gates.passes(1.0, 2, 0.0, 0.5)
    assert not gates.passes(-1.0, 2, 0.0, 0.5)
    assert not gates.passes(1.0, 0, 0.0, 0.5)


def test_build_knowledge_graph_and_persist_idempotent(tmp_path, monkeypatch):
    from research_pipeline.document_ingestion import build_knowledge_graph, graph_to_kg_records
    from research_pipeline.knowledge_graph import get_related_events, persist_graph_slice

    monkeypatch.setattr(
        "data_layer.openfoundry_bridge.validate_connector",
        lambda repo_root: {"upstream": {"core_pack_present": True}},
    )

    text = "CPI release affects MES and ES. Must not use lookahead."
    g = build_knowledge_graph(text, doc_id="doc:test")
    records = graph_to_kg_records(g)
    assert any(n.get("type") == "macro-event" for n in records["nodes"])

    kg_dir = tmp_path / "research_cards" / "kg"
    kg_dir.mkdir(parents=True)
    (kg_dir / "nodes.jsonl").write_text("", encoding="utf-8")
    (kg_dir / "edges.jsonl").write_text("", encoding="utf-8")

    n1, e1 = persist_graph_slice(tmp_path, g)
    n2, e2 = persist_graph_slice(tmp_path, g)
    assert n1 >= 1
    assert n2 == 0
    assert e2 == 0

    events = get_related_events(tmp_path, "MES")
    assert isinstance(events, list)


def test_run_pipeline_dry_run():
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), str(REPO / "packages"), str(REPO / "apps")]
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "run_pipeline.py"),
            "--thesis",
            "Fade spread blowout after CPI",
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "3",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("response_packet", {}).get("llm_status") == "skipped_no_llm"
    assert "response_packet" in data or "parsed" in data
    if "parsed" in data:
        assert data["parsed"]["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    if "candidates" in data:
        assert len(data["candidates"]) == 3
    elif data.get("response_packet"):
        assert data["response_packet"]["parsed"]["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_pipeline_request_response_roundtrip():
    from research_pipeline.packets import build_pipeline_request, build_pipeline_response
    from research_pipeline.types import PipelineReport, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="test",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=["spread"],
        feature_list=[],
        param_ranges={},
        primary_model_id="HYP_5",
        source="heuristic",
    )
    report = PipelineReport(
        run_id="pipeline_test",
        thesis="test",
        event_id="CPI_2024_09_11_TIGHT",
        parsed=parsed,
        candidates_tested=0,
        results=[],
        selected=None,
        artifact_dir=None,
    )
    req = build_pipeline_request(
        request_id="pipeline_test",
        thesis="test",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    resp = build_pipeline_response(report, req, llm_status="ok")
    assert resp["request_id"] == "pipeline_test"
    assert resp["parsed"]["primary_model_id"] == "HYP_5"


def test_hypothesis_packet_strict_mock(monkeypatch):
    from data_layer.llm import ollama_client
    from data_layer.llm.packet_runner import run_llm_on_hypothesis_request
    from research_pipeline.packets import build_pipeline_request

    request = build_pipeline_request(
        request_id="req_hyp",
        thesis="Fade spread blowout after CPI",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    mock_body = json.dumps(
        {
            "schema_version": "1",
            "request_id": "req_hyp",
            "llm_model": "mock-glm",
            "llm_status": "ok",
            "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "instrument_universe": ["MES"],
            "entry_rules": ["enter on signal"],
            "exit_rules": ["exit on revert"],
            "indicators": ["spread"],
            "feature_list": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "param_ranges": {"signal_threshold": [0.05, 0.35]},
        }
    )
    monkeypatch.setattr(ollama_client, "ollama_available", lambda **kw: True)
    monkeypatch.setattr(
        ollama_client,
        "generate",
        lambda *a, **k: ollama_client.GenerateResult(mock_body, model="mock-glm", elapsed_s=0.1),
    )
    out = run_llm_on_hypothesis_request(
        request,
        "Fade spread blowout after CPI",
        allowed_model_ids=["SPREAD_BLOWOUT_RECOMPRESSION", "HYP_5"],
        repo_root=REPO,
    )
    assert out["llm_status"] == "ok"
    assert out["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_parse_hypothesis_uses_packet_runner(monkeypatch):
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.packets import build_pipeline_request

    request = build_pipeline_request(
        request_id="req_parse",
        thesis="Fade spread blowout after CPI",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    monkeypatch.setattr(
        "data_layer.llm.packet_runner.run_llm_on_hypothesis_request",
        lambda *a, **k: {
            "llm_status": "ok",
            "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "instrument_universe": ["MES"],
            "entry_rules": [],
            "exit_rules": [],
            "indicators": [],
            "feature_list": [],
            "param_ranges": {"signal_threshold": [0.05, 0.35]},
        },
    )
    parsed = parse_hypothesis(
        "Fade spread blowout after CPI",
        pipeline_request=request,
        repo_root=REPO,
    )
    assert parsed.primary_model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert parsed.source == "ollama"


NPZ = REPO / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"


@pytest.mark.skipif(not NPZ.is_file(), reason="CPI NPZ not present locally")
def test_evaluate_model_smoke():
    from research_pipeline.evaluation import evaluate_model
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.model_generation import generate_candidates
    from research_pipeline.types import GateThresholds

    parsed = parse_hypothesis("spread blowout", use_llm=False)
    cand = next(generate_candidates(parsed, max_candidates=1))
    result = evaluate_model(
        cand,
        "CPI_2024_09_11_TIGHT",
        REPO,
        gates=GateThresholds(min_trades=0),
    )
    assert result.error is None
    assert result.num_trades >= 0


def test_vendor_submodules_present():
    """OpenFoundry + AlphaGeometry must be vendored in-repo (not confused with Ollama cloud LLMs)."""
    assert (REPO / "vendor" / "openfoundry" / "domain-packs" / "core" / "pack.yaml").is_file()
    assert (REPO / "vendor" / "alphageometry").is_dir()
    lock = (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_text(encoding="utf-8")
    assert "openfoundry=" in lock
    assert "alphageometry=" in lock
