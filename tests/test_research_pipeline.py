"""Tests for packages/research_pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "docs" / "references" / "dev_instructions.pdf"


def _loads_json_payload(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0, stdout
    return json.loads(stdout[start:])


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


def test_generate_candidates_random_search_uses_default_range():
    import random

    from research_pipeline.model_generation import generate_candidates
    from research_pipeline.types import ParsedHypothesis

    random.seed(7)
    parsed = ParsedHypothesis(
        thesis="random search",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )

    cands = list(
        generate_candidates(
            parsed,
            max_candidates=4,
            search_mode="random",
            num_samples=4,
        )
    )
    thresholds = [c.strategy_params["signal_threshold"] for c in cands]

    assert len(thresholds) == 4
    assert len(set(thresholds)) > 1
    assert all(0.05 <= threshold <= 0.50 for threshold in thresholds)


def test_optimizer_proposes_hyperparameter_candidates_from_prior_results():
    from research_pipeline.optimizer import propose_optimized_candidates, score_result
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="optimize spread",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    gate = GateThresholds(min_net_pnl=0.0, min_trades=1, min_sharpe=0.5)
    weak = EvaluationResult(
        candidate=CandidateModel(
            candidate_id="weak",
            model_id=parsed.primary_model_id,
            strategy_params={"signal_threshold": 0.08, "holding_period_bars": 5},
            thesis=parsed.thesis,
        ),
        event_id="EVT",
        net_pnl=-1.0,
        num_trades=2,
        win_rate=0.1,
        expectancy=-0.5,
        tail_loss=0.0,
        sharpe=0.1,
        gates=gate,
    )
    strong = EvaluationResult(
        candidate=CandidateModel(
            candidate_id="strong",
            model_id=parsed.primary_model_id,
            strategy_params={
                "signal_threshold": 0.22,
                "holding_period_bars": 30,
                "stop_loss_pct": 0.8,
                "take_profit_pct": 1.4,
            },
            thesis=parsed.thesis,
            metadata={"idea_id": "idea_strong", "idea_lane_code": "cme"},
        ),
        event_id="EVT",
        net_pnl=3.0,
        num_trades=4,
        win_rate=0.75,
        expectancy=1.2,
        tail_loss=0.0,
        sharpe=1.1,
        drawdown_bps=20.0,
        avg_latency_us=100.0,
        gates=gate,
    )

    candidates, trace = propose_optimized_candidates(
        parsed,
        [weak, strong],
        max_candidates=4,
        iteration=2,
        backend="heuristic",
        random_seed=17,
        top_k=1,
    )

    assert score_result(strong) > score_result(weak)
    assert trace.backend == "heuristic"
    assert trace.best_prior_candidate_id == "strong"
    assert len(candidates) == 4
    for candidate in candidates:
        params = candidate.strategy_params
        assert 0.05 <= params["signal_threshold"] <= 0.35
        assert 5 <= params["holding_period_bars"] <= 60
        assert 0.25 <= params["stop_loss_pct"] <= 2.0
        assert 0.25 <= params["take_profit_pct"] <= 2.5
        assert candidate.metadata["optimized"] is True
        assert candidate.metadata["idea_id"] == "idea_strong"
        assert candidate.metadata["idea_lane_code"] == "cme"
        assert candidate.metadata["optimizer_anchor_candidate_id"] == "strong"


def _idea_packet():
    return {
        "schema_version": "1",
        "request_id": "req_idea",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {
            "ref_event": {"type": "event", "value": "CPI_2024_09_11_TIGHT"},
            "mem_001": {"type": "artifact", "value": "artifacts/run/after_action_response.json"},
        },
        "constraints": {
            "allowed_model_ids": [
                "SPREAD_BLOWOUT_RECOMPRESSION",
                "BOOK_PRESSURE",
            ],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 4,
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
                "idea_id": "idea_low",
                "status": "proposed",
                "lane_code": "cme",
                "thesis_code": "book_pressure",
                "instrument_ids": ["MES"],
                "primary_model_id": "BOOK_PRESSURE",
                "feature_ids": ["pdf.ofi_pca.ofi_value"],
                "param_ranges": {"signal_threshold": [0.05, 0.35]},
                "entry_rule_codes": ["enter_pressure"],
                "exit_rule_codes": ["exit_revert"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": ["mem_001"],
                "rank_inputs": {
                    "novelty": 0.1,
                    "evidence_coverage": 0.1,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            },
            {
                "idea_id": "idea_high",
                "status": "proposed",
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
                    "novelty": 0.6,
                    "evidence_coverage": 0.6,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            },
            {
                "idea_id": "idea_bad",
                "status": "proposed",
                "lane_code": "cme",
                "thesis_code": "bad_ref",
                "instrument_ids": ["MES"],
                "primary_model_id": "NOT_A_MODEL",
                "feature_ids": ["NOT_A_MODEL"],
                "param_ranges": {"signal_threshold": [0.05, 0.35]},
                "entry_rule_codes": ["enter_bad"],
                "exit_rule_codes": ["exit_bad"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": ["mem_001"],
                "rank_inputs": {
                    "novelty": 1.0,
                    "evidence_coverage": 1.0,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            },
        ],
    }


def test_idea_static_filter_rejects_invalid_and_orders_queue():
    from research_pipeline.idea_generation import candidates_from_ideas, idea_summary, parsed_from_idea

    packet = _idea_packet()
    packet["ideas"][1]["param_ranges"] = {"signal_threshold": [0.90, 0.95]}
    candidates = candidates_from_ideas(packet, max_candidates=2)

    by_id = {idea["idea_id"]: idea for idea in packet["ideas"]}
    assert by_id["idea_bad"]["status"] == "static_reject"
    assert "primary_model_id_not_allowed" in by_id["idea_bad"]["static_error_codes"]
    assert [c.metadata["idea_id"] for c in candidates] == ["idea_high", "idea_low"]
    assert candidates[0].strategy_params["signal_threshold"] == 0.05
    assert parsed_from_idea(by_id["idea_high"]).param_ranges == {"signal_threshold": [0.05, 0.35]}
    assert idea_summary(packet, candidates_from_ideas_count=len(candidates)) == {
        "ideas_generated": 3,
        "ideas_static_rejected": 1,
        "ideas_queued_for_test": 2,
        "ideas_tested_fail": 0,
        "ideas_tested_pass": 0,
        "candidates_from_ideas": 2,
    }


def test_idea_feature_ids_do_not_expand_candidate_model_families():
    from research_pipeline.idea_generation import candidates_from_ideas, parsed_from_idea
    from research_pipeline.model_generation import generate_candidates

    packet = _idea_packet()
    packet["constraints"]["allowed_model_ids"] = ["SPREAD_BLOWOUT_RECOMPRESSION"]
    packet["constraints"]["max_candidates"] = 6
    packet["ideas"] = [
        {
            "idea_id": "idea_feature_ref",
            "status": "proposed",
            "lane_code": "cme",
            "thesis_code": "spread_recompression_uses_book_pressure_context",
            "instrument_ids": ["MES"],
            "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "feature_ids": ["mbo.depth.spread_stress"],
            "param_ranges": {"signal_threshold": [0.05, 0.35]},
            "entry_rule_codes": ["enter_spread_signal"],
            "exit_rule_codes": ["exit_revert"],
            "risk_codes": ["latency_gate_required"],
            "evidence_ref_ids": ["mem_001"],
            "rank_inputs": {
                "novelty": 0.6,
                "evidence_coverage": 0.6,
                "lane_fit": 1.0,
                "prior_failure_overlap": 0.0,
                "validation_readiness": 1.0,
            },
        }
    ]

    parsed = parsed_from_idea(packet["ideas"][0])
    assert parsed.indicators == ["mbo.depth.spread_stress"]
    assert parsed.feature_list == ["mbo.depth.spread_stress"]
    generated = list(generate_candidates(parsed, max_candidates=6))
    assert {candidate.model_id for candidate in generated} == {"SPREAD_BLOWOUT_RECOMPRESSION"}

    candidates = candidates_from_ideas(packet, max_candidates=6)
    assert {candidate.model_id for candidate in candidates} == {"SPREAD_BLOWOUT_RECOMPRESSION"}
    assert packet["ideas"][0]["status"] == "queued_for_test"
    assert "static_error_codes" not in packet["ideas"][0]


def test_idea_status_updates_only_from_evaluation_results():
    from research_pipeline.idea_generation import update_idea_statuses_from_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    packet = _idea_packet()
    for idea in packet["ideas"]:
        if idea["idea_id"] != "idea_bad":
            idea["status"] = "queued_for_test"
    gate = GateThresholds(min_net_pnl=0.0, min_trades=1)
    results = [
        EvaluationResult(
            candidate=CandidateModel(
                candidate_id="c_fail",
                model_id="BOOK_PRESSURE",
                strategy_params={},
                thesis="x",
                metadata={"idea_id": "idea_low"},
            ),
            event_id="CPI_2024_09_11_TIGHT",
            net_pnl=-1.0,
            num_trades=2,
            win_rate=0.0,
            expectancy=-1.0,
            tail_loss=0.0,
            gates=gate,
        ),
        EvaluationResult(
            candidate=CandidateModel(
                candidate_id="c_pass",
                model_id="SPREAD_BLOWOUT_RECOMPRESSION",
                strategy_params={},
                thesis="x",
                metadata={"idea_id": "idea_high"},
            ),
            event_id="CPI_2024_09_11_TIGHT",
            net_pnl=1.0,
            num_trades=2,
            win_rate=1.0,
            expectancy=1.0,
            tail_loss=0.0,
            gates=gate,
        ),
    ]

    update_idea_statuses_from_results(packet, results)

    by_id = {idea["idea_id"]: idea for idea in packet["ideas"]}
    assert by_id["idea_low"]["status"] == "tested_fail"
    assert by_id["idea_high"]["status"] == "tested_pass"
    assert by_id["idea_bad"]["status"] == "proposed"


def test_idea_vectorbt_reject_all_marks_queued_ideas_tested_fail():
    from research_pipeline.idea_generation import (
        candidates_from_ideas,
        mark_queued_ideas_without_candidates_failed,
    )

    packet = _idea_packet()
    candidates = candidates_from_ideas(packet, max_candidates=2)
    assert candidates

    mark_queued_ideas_without_candidates_failed(packet, [])

    by_id = {idea["idea_id"]: idea for idea in packet["ideas"]}
    assert by_id["idea_high"]["status"] == "tested_fail"
    assert by_id["idea_low"]["status"] == "tested_fail"
    assert by_id["idea_bad"]["status"] == "static_reject"


def test_idea_set_deployment_requires_passing_existing_gate():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    gate = GateThresholds(min_net_pnl=0.0, min_trades=1)
    failing = EvaluationResult(
        candidate=CandidateModel(
            candidate_id="c_fail",
            model_id="BOOK_PRESSURE",
            strategy_params={},
            thesis="x",
            metadata={"idea_id": "idea_low"},
        ),
        event_id="CPI_2024_09_11_TIGHT",
        net_pnl=-1.0,
        num_trades=2,
        win_rate=0.0,
        expectancy=-1.0,
        tail_loss=0.0,
        gates=gate,
    )
    passing = EvaluationResult(
        candidate=CandidateModel(
            candidate_id="c_pass",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={},
            thesis="x",
            metadata={"idea_id": "idea_high"},
        ),
        event_id="CPI_2024_09_11_TIGHT",
        net_pnl=1.0,
        num_trades=2,
        win_rate=1.0,
        expectancy=1.0,
        tail_loss=0.0,
        gates=gate,
    )

    assert run_pipeline._deployment_allowed(False, [failing]) is True
    assert run_pipeline._deployment_allowed(True, [failing]) is False
    assert run_pipeline._deployment_allowed(True, [failing, passing]) is True
    assert run_pipeline._idea_set_missing_prefilter(
        idea_set_enabled=True,
        dry_run=False,
        vectorbt=False,
        vectorbt_only=False,
    )
    assert not run_pipeline._idea_set_missing_prefilter(
        idea_set_enabled=True,
        dry_run=False,
        vectorbt=True,
        vectorbt_only=False,
    )
    assert not run_pipeline._idea_set_missing_prefilter(
        idea_set_enabled=True,
        dry_run=True,
        vectorbt=False,
        vectorbt_only=False,
    )


def test_gate_thresholds():
    from research_pipeline.types import GateThresholds

    gates = GateThresholds(min_net_pnl=0.0, min_trades=1)
    assert gates.passes(1.0, 2, 0.0, 0.5)
    assert not gates.passes(-1.0, 2, 0.0, 0.5)
    assert not gates.passes(1.0, 0, 0.0, 0.5)

    metric_gates = GateThresholds(
        min_sharpe=1.0,
        max_drawdown_bps=25.0,
        max_avg_latency_us=500.0,
    )
    assert metric_gates.passes(
        1.0,
        2,
        0.0,
        0.5,
        sharpe=1.1,
        drawdown_bps=20.0,
        avg_latency_us=100.0,
    )
    assert not metric_gates.passes(1.0, 2, 0.0, 0.5)
    assert not metric_gates.passes(
        1.0,
        2,
        0.0,
        0.5,
        sharpe=0.9,
        drawdown_bps=20.0,
        avg_latency_us=100.0,
    )

    risk_gates = GateThresholds(
        min_sharpe=1.0,
        max_drawdown_bps=50.0,
        max_avg_latency_us=250.0,
    )
    assert risk_gates.passes(
        1.0,
        2,
        0.0,
        0.5,
        sharpe=1.5,
        drawdown_bps=25.0,
        avg_latency_us=100.0,
    )
    assert not risk_gates.passes(1.0, 2, 0.0, 0.5, sharpe=0.5)
    assert not risk_gates.passes(1.0, 2, 0.0, 0.5, drawdown_bps=75.0)
    assert not risk_gates.passes(1.0, 2, 0.0, 0.5, avg_latency_us=300.0)


def test_evaluate_model_extracts_extended_gate_metrics(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    import research_pipeline.evaluation as evaluation
    from research_pipeline.types import CandidateModel, GateThresholds

    class FakeWorkbenchEngine:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def run(self, *args, **kwargs):
            return {
                "report": {
                    "net_pnl": 3.0,
                    "num_trades": 4,
                    "max_drawdown_pct": -0.25,
                    "latency_authority": {"avg_latency_us": 175.0},
                },
                "diagnostics": {
                    "win_rate": 0.75,
                    "expectancy": 0.5,
                    "tail_loss": 0.1,
                    "sharpe_ratio": 1.25,
                },
            }

    fake_engine_module = ModuleType("workbench.src.run.engine")
    fake_engine_module.WorkbenchEngine = FakeWorkbenchEngine
    monkeypatch.setitem(sys.modules, "workbench.src.run.engine", fake_engine_module)
    monkeypatch.setattr(evaluation, "resolve_model_id", lambda model_id: model_id)

    result = evaluation.evaluate_model(
        CandidateModel(
            candidate_id="cand_metrics",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={},
            thesis="metrics",
        ),
        "CPI_2024_09_11_TIGHT",
        tmp_path,
        gates=GateThresholds(min_sharpe=1.0, max_drawdown_bps=30.0, max_avg_latency_us=200.0),
    )

    assert result.error is None
    assert result.sharpe == 1.25
    assert result.drawdown_bps == 25.0
    assert result.avg_latency_us == 175.0
    assert result.passes_all_gates()


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
    data = _loads_json_payload(proc.stdout)
    assert data.get("response_packet", {}).get("llm_status") == "skipped_no_llm"
    assert "response_packet" in data or "parsed" in data
    if "parsed" in data:
        assert data["parsed"]["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    if "candidates" in data:
        assert len(data["candidates"]) <= 3
    elif data.get("response_packet"):
        assert data["response_packet"]["parsed"]["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert "idea_set_packet" in data


@pytest.mark.parametrize("lane_args", [[], ["--lane", "crypto"]])
def test_run_pipeline_ignores_vectorbt_only_and_runs_full_idea_set_pipeline(
    tmp_path, monkeypatch, lane_args
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="idea_set",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
        metadata={"idea_id": "idea_001"},
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_vbt_only",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {
            "connector_id": "hft3-cme-mbo",
            "asset_class": "cme_mbo_microstructure",
            "vendor_shas": {"openfoundry": "test"},
            "schema_version": "1",
        },
        "max_candidates": 1,
    }
    idea_packet = {
        "schema_version": "1",
        "request_id": "pipeline_vbt_only",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {},
        "constraints": {
            "allowed_model_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 1,
            "no_promotion_authority": True,
        },
        "review_memory": [],
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
                "entry_rule_codes": ["enter_spread"],
                "exit_rule_codes": ["exit_revert"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": [],
                "rank_inputs": {
                    "novelty": 0.1,
                    "evidence_coverage": 0.0,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            }
        ],
    }

    def fake_filter_candidates(*args, **kwargs):
        return FilterResult(
            promoted=[
                PromotedCandidate(
                    candidate_id="cand_vbt",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                    asset_class="CME",
                    symbol="MES",
                    timeframe="1m",
                    param_values={"signal_threshold": 0.1},
                    vectorbt_run_id="vbt_test",
                    vectorbt_results={"oos_expectancy": 1.0, "num_trades": 3},
                    pass_reason="all_gates_passed",
                )
            ],
            rejected=[],
            vectorbt_available=True,
            backend="test",
            run_id="vbt_test",
            total_candidates=7,
        )

    idea_set_calls = []
    evaluate_calls = []
    deploy_calls = []
    gate = GateThresholds(min_net_pnl=0.0, min_trades=1)

    def fake_generate_idea_set(*args, **kwargs):
        idea_set_calls.append(kwargs)
        return idea_packet

    def fake_evaluate_model(cand, event_id, repo_root, **kwargs):
        evaluate_calls.append((cand, event_id, repo_root, kwargs))
        return EvaluationResult(
            candidate=cand,
            event_id=event_id,
            net_pnl=1.0,
            num_trades=2,
            win_rate=1.0,
            expectancy=1.0,
            tail_loss=0.0,
            gates=gate,
        )

    def fake_deploy_best(repo_root, report):
        deploy_calls.append((repo_root, report))
        return repo_root / "deployed.json"

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_only")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "generate_idea_set", fake_generate_idea_set)
    monkeypatch.setattr(run_pipeline, "candidates_from_ideas", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "parsed_from_idea", lambda idea: parsed)
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(run_pipeline, "evaluate_model", fake_evaluate_model)
    monkeypatch.setattr(run_pipeline, "deploy_best", fake_deploy_best)

    argv = [
        "run_pipeline.py",
        "--thesis",
        parsed.thesis,
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--max-candidates",
        "1",
        "--no-llm",
        "--vectorbt-only",
    ]
    argv.extend(lane_args)
    monkeypatch.setattr(sys, "argv", argv)

    assert run_pipeline.main() == 0
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_vbt_only"
    response = json.loads((run_dir / "response_packet.json").read_text(encoding="utf-8"))
    idea_packet_out = json.loads((run_dir / "idea_set_packet.json").read_text(encoding="utf-8"))

    assert idea_set_calls
    assert len(evaluate_calls) == 1
    assert len(deploy_calls) == 1
    assert response["candidates_tested"] == 1
    assert response["results"] == [
        {
            "candidate_id": "cand_vbt",
            "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "net_pnl": 1.0,
            "num_trades": 2,
            "passes": True,
            "error": None,
        }
    ]
    assert response["selected_model_id"] is None
    assert response["idea_summary"]["candidates_from_ideas"] == 1
    assert idea_packet_out["ideas"][0]["status"] == "tested_pass"
    assert not (run_dir / "vectorbt_filter.json").exists()


def test_run_pipeline_adaptive_retry_expands_search_after_gate_failure(tmp_path, monkeypatch):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Retry failed candidates",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="idea_set",
    )
    initial_candidate = CandidateModel(
        candidate_id="cand_initial",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
        metadata={"idea_id": "idea_retry"},
    )
    retry_candidate = CandidateModel(
        candidate_id="cand_retry",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.3333},
        thesis=parsed.thesis,
        metadata={},
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_retry",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {
            "connector_id": "hft3-cme-mbo",
            "asset_class": "cme_mbo_microstructure",
            "vendor_shas": {"openfoundry": "test"},
            "schema_version": "1",
        },
        "max_candidates": 1,
    }
    idea_packet = {
        "schema_version": "1",
        "request_id": "pipeline_retry",
        "llm_model": "mock",
        "llm_status": "ok",
        "refs": {},
        "constraints": {
            "allowed_model_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "allowed_lane_codes": ["cme"],
            "max_candidates": 1,
            "no_promotion_authority": True,
        },
        "review_memory": [],
        "ideas": [
            {
                "idea_id": "idea_retry",
                "status": "queued_for_test",
                "lane_code": "cme",
                "thesis_code": "spread_recompression",
                "instrument_ids": ["MES"],
                "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "feature_ids": ["mbo.depth.spread_stress"],
                "param_ranges": {"signal_threshold": [0.05, 0.35]},
                "entry_rule_codes": ["enter_spread"],
                "exit_rule_codes": ["exit_revert"],
                "risk_codes": ["latency_gate_required"],
                "evidence_ref_ids": [],
                "rank_inputs": {
                    "novelty": 0.1,
                    "evidence_coverage": 0.0,
                    "lane_fit": 1.0,
                    "prior_failure_overlap": 0.0,
                    "validation_readiness": 1.0,
                },
            }
        ],
    }

    filter_calls = []
    idea_candidate_calls = []
    optimizer_calls = []
    evaluate_calls = []
    gate = GateThresholds(min_net_pnl=0.0, min_trades=1)

    def fake_candidates_from_ideas(*args, **kwargs):
        idea_candidate_calls.append(kwargs)
        return [initial_candidate]

    def fake_propose_optimized_candidates(*args, **kwargs):
        optimizer_calls.append((args, kwargs))
        return [
            retry_candidate
        ], type(
            "Trace",
            (),
            {
                "to_dict": lambda self: {
                    "backend": kwargs["backend"],
                    "iteration": kwargs["iteration"],
                    "requested_candidates": kwargs["max_candidates"],
                    "emitted_candidates": 1,
                    "best_prior_candidate_id": args[1][0].candidate.candidate_id,
                    "best_prior_score": -1.0,
                    "fallback_reason": None,
                }
            },
        )()

    def fake_filter_candidates(*args, **kwargs):
        candidates = kwargs["candidates"]
        filter_calls.append([candidate.candidate_id for candidate in candidates])
        promoted = [
            PromotedCandidate(
                candidate_id=candidates[0].candidate_id,
                hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                asset_class="CME",
                symbol="MES",
                timeframe="1m",
                param_values=candidates[0].strategy_params,
                vectorbt_run_id=f"vbt_{len(filter_calls)}",
                vectorbt_results={"oos_expectancy": 1.0, "num_trades": 10},
                pass_reason="all_gates_passed",
            )
        ]
        return FilterResult(
            promoted=promoted,
            rejected=[],
            vectorbt_available=True,
            backend="test",
            run_id=f"vbt_{len(filter_calls)}",
            total_candidates=len(candidates),
        )

    def fake_evaluate_model(cand, event_id, repo_root, **kwargs):
        evaluate_calls.append(cand.candidate_id)
        net_pnl = -1.0 if cand.candidate_id == "cand_initial" else 2.0
        return EvaluationResult(
            candidate=cand,
            event_id=event_id,
            net_pnl=net_pnl,
            num_trades=2,
            win_rate=1.0,
            expectancy=net_pnl,
            tail_loss=0.0,
            gates=gate,
        )

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_retry")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: idea_packet)
    monkeypatch.setattr(run_pipeline, "candidates_from_ideas", fake_candidates_from_ideas)
    monkeypatch.setattr(run_pipeline, "parsed_from_idea", lambda idea: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: pytest.fail("fallback generator should not run for idea-set retry"))
    monkeypatch.setattr(run_pipeline, "propose_optimized_candidates", fake_propose_optimized_candidates)
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(run_pipeline, "evaluate_model", fake_evaluate_model)
    monkeypatch.setattr(run_pipeline, "deploy_best", lambda repo_root, report: repo_root / "deployed.json")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_pipeline.py",
            "--thesis",
            parsed.thesis,
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--max-candidates",
            "1",
            "--no-llm",
            "--search-mode",
            "grid",
            "--num-samples",
            "4",
            "--max-iterations",
            "3",
            "--optimizer-backend",
            "heuristic",
            "--random-seed",
            "99",
        ],
    )

    assert run_pipeline.main() == 0

    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_retry"
    response = json.loads((run_dir / "response_packet.json").read_text(encoding="utf-8"))
    optimization_trace = json.loads((run_dir / "optimization_trace.json").read_text(encoding="utf-8"))

    assert filter_calls == [["cand_initial"], ["cand_retry"]]
    assert idea_candidate_calls[0]["search_mode"] == "grid"
    assert idea_candidate_calls[0]["max_candidates"] == 1
    assert idea_candidate_calls[0]["num_samples"] == 4
    assert idea_candidate_calls[0]["max_iterations"] == 1
    assert len(idea_candidate_calls) == 1
    assert optimizer_calls[0][0][1][0].candidate.candidate_id == "cand_initial"
    assert optimizer_calls[0][1]["backend"] == "heuristic"
    assert optimizer_calls[0][1]["max_candidates"] == 8
    assert optimizer_calls[0][1]["iteration"] == 2
    assert evaluate_calls == ["cand_initial", "cand_retry"]
    assert response["candidates_tested"] == 2
    assert [result["passes"] for result in response["results"]] == [False, True]
    assert optimization_trace == [
        {
            "backend": "heuristic",
            "iteration": 2,
            "requested_candidates": 8,
            "emitted_candidates": 1,
            "best_prior_candidate_id": "cand_initial",
            "best_prior_score": -1.0,
            "fallback_reason": None,
        }
    ]


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
    from data_layer.llm import openai_compatible_client as llm_client
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
            "llm_model": "mock-gpt55",
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
    monkeypatch.setattr(llm_client, "llm_available", lambda **kw: True)
    monkeypatch.setattr(
        llm_client,
        "generate",
        lambda *a, **k: llm_client.GenerateResult(mock_body, model="mock-gpt55", elapsed_s=0.1),
    )
    out = run_llm_on_hypothesis_request(
        request,
        "Fade spread blowout after CPI",
        allowed_model_ids=["SPREAD_BLOWOUT_RECOMPRESSION", "HYP_5"],
        repo_root=REPO,
    )
    assert out["llm_status"] == "ok"
    assert out["primary_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_idea_generation_llm_uses_sampling_controls(monkeypatch):
    from data_layer.llm import openai_compatible_client as llm_client
    from data_layer.llm.packet_runner import run_llm_on_idea_generation_request
    from research_pipeline.packets import build_pipeline_request

    request = build_pipeline_request(
        request_id="req_idea_llm",
        thesis="Fade spread blowout after CPI",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    mock_body = json.dumps(
        {
            "schema_version": "1",
            "request_id": "req_idea_llm",
            "llm_model": "mock-gpt55",
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
                    "status": "proposed",
                    "lane_code": "cme",
                    "thesis_code": "spread_recompression",
                    "instrument_ids": ["MES"],
                    "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                    "feature_ids": ["mbo.depth.spread_stress"],
                    "param_ranges": {"signal_threshold": [0.05, 0.35]},
                    "entry_rule_codes": ["enter_spread"],
                    "exit_rule_codes": ["exit_revert"],
                    "risk_codes": ["latency_gate_required"],
                    "evidence_ref_ids": [],
                    "rank_inputs": {
                        "novelty": 0.1,
                        "evidence_coverage": 0.0,
                        "lane_fit": 1.0,
                        "prior_failure_overlap": 0.0,
                        "validation_readiness": 1.0,
                    },
                }
            ],
        }
    )
    captured = {}
    monkeypatch.setattr(llm_client, "llm_available", lambda **kw: True)

    def fake_generate(*args, **kwargs):
        captured.update(kwargs)
        return llm_client.GenerateResult(mock_body, model="mock-gpt55", elapsed_s=0.1)

    monkeypatch.setattr(llm_client, "generate", fake_generate)
    out = run_llm_on_idea_generation_request(
        request,
        "Fade spread blowout after CPI",
        allowed_model_ids=["SPREAD_BLOWOUT_RECOMPRESSION"],
        allowed_lane_codes=["cme"],
        review_memory=[],
        refs={"ref_event": {"type": "event", "value": "CPI_2024_09_11_TIGHT"}},
        max_candidates=3,
        temperature=0.7,
        top_p=0.95,
    )

    assert out["llm_status"] == "ok"
    assert captured["temperature"] == 0.7
    assert captured["top_p"] == 0.95


def test_hypothesis_llm_does_not_use_idea_sampling_controls(monkeypatch):
    from data_layer.llm import openai_compatible_client as llm_client
    from data_layer.llm.packet_runner import run_llm_on_hypothesis_request
    from research_pipeline.packets import build_pipeline_request

    request = build_pipeline_request(
        request_id="req_hyp_no_sampling",
        thesis="Fade spread blowout after CPI",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    mock_body = json.dumps(
        {
            "schema_version": "1",
            "request_id": "req_hyp_no_sampling",
            "llm_model": "mock-gpt55",
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
    captured = {}
    monkeypatch.setattr(llm_client, "llm_available", lambda **kw: True)

    def fake_generate(*args, **kwargs):
        captured.update(kwargs)
        return llm_client.GenerateResult(mock_body, model="mock-gpt55", elapsed_s=0.1)

    monkeypatch.setattr(llm_client, "generate", fake_generate)
    out = run_llm_on_hypothesis_request(
        request,
        "Fade spread blowout after CPI",
        allowed_model_ids=["SPREAD_BLOWOUT_RECOMPRESSION"],
        repo_root=REPO,
    )

    assert out["llm_status"] == "ok"
    assert "temperature" not in captured
    assert "top_p" not in captured


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
    assert parsed.source == "openai_compatible"


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
    """OpenFoundry + AlphaGeometry must be vendored in-repo, not confused with runtime LLMs."""
    assert (REPO / "vendor" / "openfoundry" / "domain-packs" / "core" / "pack.yaml").is_file()
    assert (REPO / "vendor" / "alphageometry").is_dir()
    lock = (REPO / "integrations" / "openfoundry" / "VENDOR.lock").read_text(encoding="utf-8")
    assert "openfoundry=" in lock
    assert "alphageometry=" in lock
