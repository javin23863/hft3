"""Tests for packages/research_pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "docs" / "references" / "dev_instructions.pdf"


def _last_json_object(stdout: str) -> dict:
    start = stdout.rfind("\n{")
    if start == -1:
        start = stdout.find("{")
    else:
        start += 1
    assert start != -1, stdout
    return json.loads(stdout[start:])


def test_hftbacktest_realism_preflight_requires_source_lock_and_native_evidence():
    import argparse

    import scripts.run_pipeline as run_pipeline

    args = argparse.Namespace(
        hftbacktest_data_npz=Path("data.npz"),
        hftbacktest_latency_model=Path("latency.json"),
        hftbacktest_fill_queue_model=Path("fill_queue.json"),
        hftbacktest_upstream_ref=None,
        native_hot_path_evidence=[],
    )

    assert run_pipeline._missing_hftbacktest_realism_inputs(args) == [
        "--hftbacktest-upstream-ref",
        "--native-hot-path-evidence",
    ]


def test_run_pipeline_hftbacktest_realism_requires_vectorbt(monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_request",
        lambda **kwargs: pytest.fail("hftbacktest-realism without vectorbt wrote artifacts"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade CPI blowout",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--no-llm",
        "--hftbacktest-realism",
    ])

    assert run_pipeline.main() == 2
    assert "--hftbacktest-realism requires --vectorbt" in capsys.readouterr().err


def test_run_pipeline_hftbacktest_realism_rejects_vectorbt_only(monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_request",
        lambda **kwargs: pytest.fail("hftbacktest-realism vectorbt-only wrote artifacts"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade CPI blowout",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--no-llm",
        "--vectorbt-only",
        "--hftbacktest-realism",
    ])

    assert run_pipeline.main() == 2
    assert "--hftbacktest-realism cannot be combined with --vectorbt-only" in capsys.readouterr().err


def test_run_pipeline_doc_without_vectorbt_is_dry_run_only(monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_request",
        lambda **kwargs: pytest.fail("doc ingestion without vectorbt/dry-run wrote artifacts"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade CPI blowout",
        "--doc",
        str(PDF),
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--no-llm",
    ])

    assert run_pipeline.main() == 2
    assert "--doc without --vectorbt/--vectorbt-only is dry-run only" in capsys.readouterr().err


def test_pipeline_runtime_config_defaults_from_json(tmp_path):
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg_path = tmp_path / "runtime.json"
    cfg_path.write_text(
        json.dumps(
            {
                "max_candidates": 7,
                "vectorbt": {
                    "scope": "paid-compute",
                    "budget": {
                        "max_trials": 3,
                        "max_total_trials": 21,
                        "abort_on_budget_exhaustion": True,
                    },
                },
                "llm_ideas": {"max_ideas": 4, "review_memory_limit": 2},
            }
        ),
        encoding="utf-8",
    )
    cfg = run_pipeline.load_pipeline_runtime_config(cfg_path)
    args = argparse.Namespace(
        max_candidates=None,
        vectorbt_scope=None,
        vectorbt_max_trials=None,
        vectorbt_max_models=None,
        vectorbt_max_symbols=None,
        vectorbt_max_feature_sets=None,
        vectorbt_max_total_trials=None,
        vectorbt_max_wall_clock_seconds=None,
        vectorbt_max_peak_memory_mb=None,
        max_ideas=None,
        review_memory_limit=None,
    )

    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)

    assert args.max_candidates == 7
    assert args.vectorbt_scope == "paid-compute"
    assert args.vectorbt_max_trials == 3
    assert args.vectorbt_max_total_trials == 21
    assert args.max_ideas == 4
    assert args.review_memory_limit == 2
    assert run_pipeline._vectorbt_run_budget(args, cfg) == {
        "max_trials": 3,
        "max_total_trials": 21,
        "abort_on_budget_exhaustion": True,
    }


def test_pipeline_runtime_config_rejects_false_abort_policy(tmp_path):
    import argparse

    import pytest
    import scripts.run_pipeline as run_pipeline

    cfg_path = tmp_path / "runtime.json"
    cfg_path.write_text(
        json.dumps({"vectorbt": {"budget": {"abort_on_budget_exhaustion": False}}}),
        encoding="utf-8",
    )
    cfg = run_pipeline.load_pipeline_runtime_config(cfg_path)
    args = argparse.Namespace(
        max_candidates=None,
        vectorbt_scope=None,
        vectorbt_max_trials=None,
        vectorbt_max_models=None,
        vectorbt_max_symbols=None,
        vectorbt_max_feature_sets=None,
        vectorbt_max_total_trials=None,
        vectorbt_max_wall_clock_seconds=None,
        vectorbt_max_peak_memory_mb=None,
        max_ideas=None,
        review_memory_limit=None,
    )

    with pytest.raises(ValueError, match="abort_on_budget_exhaustion must be true"):
        run_pipeline._apply_pipeline_runtime_defaults(args, cfg)


def test_pipeline_runtime_config_hash_includes_idea_sampling_override(tmp_path):
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg = run_pipeline.load_pipeline_runtime_config()

    def make_args(temperature):
        return argparse.Namespace(
            max_candidates=None,
            vectorbt_scope=None,
            vectorbt_max_trials=None,
            vectorbt_max_models=None,
            vectorbt_max_symbols=None,
            vectorbt_max_feature_sets=None,
            vectorbt_max_total_trials=None,
            vectorbt_max_wall_clock_seconds=None,
            vectorbt_max_peak_memory_mb=None,
            max_ideas=None,
            review_memory_limit=None,
            idea_temperature=temperature,
            idea_top_p=None,
        )

    args_a = make_args(0.11)
    run_pipeline._apply_pipeline_runtime_defaults(args_a, cfg)
    run_pipeline._resolve_idea_sampling_values(args_a, cfg)
    receipt_a = run_pipeline._pipeline_config_receipt(
        config=cfg,
        config_path=tmp_path / "runtime.json",
        args=args_a,
    )

    args_b = make_args(0.22)
    run_pipeline._apply_pipeline_runtime_defaults(args_b, cfg)
    run_pipeline._resolve_idea_sampling_values(args_b, cfg)
    receipt_b = run_pipeline._pipeline_config_receipt(
        config=cfg,
        config_path=tmp_path / "runtime.json",
        args=args_b,
    )

    assert receipt_a["effective"]["llm_ideas"]["temperature"] == 0.11
    assert receipt_b["effective"]["llm_ideas"]["temperature"] == 0.22
    assert receipt_a["pipeline_runtime_config_hash"] != receipt_b["pipeline_runtime_config_hash"]


def test_candidate_prefilter_rejects_malformed_and_bounds():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel

    good = CandidateModel(
        candidate_id="good",
        model_id="BOOK_PRESSURE",
        strategy_params={"signal_threshold": 0.2, "holding_period_bars": 3},
        thesis="x",
    )
    bad = CandidateModel(
        candidate_id="bad",
        model_id="bad model",
        strategy_params={"signal_threshold": 1.2, "holding_period_bars": 0},
        thesis="x",
    )

    accepted, receipt = run_pipeline.prefilter_candidates(
        [good, bad],
        config=run_pipeline._DEFAULT_PIPELINE_RUNTIME_CONFIG["candidate_prefilter"],
    )

    assert accepted == [good]
    assert receipt["accepted_ids"] == ["good"]
    assert receipt["rejected_count"] == 1
    assert receipt["rejected"][0]["candidate_id"] == "bad"
    assert receipt["rejected"][0]["reasons"] == [
        "malformed_model_id",
        "signal_threshold_out_of_bounds",
        "holding_period_bars_nonpositive",
    ]


def test_document_ingestion_cache_miss_then_hit(tmp_path, monkeypatch):
    import scripts.run_pipeline as run_pipeline

    doc = tmp_path / "paper.txt"
    doc.write_text("CPI affects MES. Must not use lookahead.", encoding="utf-8")
    records = {
        "nodes": [{"id": "doc:paper", "type": "document"}],
        "edges": [],
    }
    calls = {"extract": 0}
    persisted = []

    def fake_extract(path):
        calls["extract"] += 1
        return doc.read_text(encoding="utf-8")

    monkeypatch.setattr(run_pipeline, "extract_text", fake_extract)
    monkeypatch.setattr(run_pipeline, "summarise_text", lambda text: "summary")
    monkeypatch.setattr(run_pipeline, "build_knowledge_graph", lambda text, doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(run_pipeline, "graph_to_kg_records", lambda graph: records)
    monkeypatch.setattr(
        run_pipeline,
        "persist_graph_slice",
        lambda repo_root, graph: persisted.append(graph) or (1, 0),
    )

    summary, meta = run_pipeline.ingest_document_with_cache(
        doc,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )
    summary2, meta2 = run_pipeline.ingest_document_with_cache(
        doc,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )
    cache_path = Path(meta["cache_path"])
    doc.touch()
    summary3, meta3 = run_pipeline.ingest_document_with_cache(
        doc,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )

    assert summary == "summary"
    assert summary2 == "summary"
    assert summary3 == "summary"
    assert meta["status"] == "miss"
    assert meta2["status"] == "hit"
    assert meta3["status"] == "hit"
    assert calls["extract"] == 1
    assert cache_path.is_file()
    assert meta3["cache_path"] == str(cache_path)
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert len(persisted) == 3


def test_run_pipeline_dry_run_writes_runtime_receipt(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="heuristic",
        llm_status="skipped_no_llm",
    )
    candidate = CandidateModel(
        candidate_id="cand_dry",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.15},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_dry_receipt",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_dry_receipt")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        },
    )

    code = run_pipeline.main(
        [
            "--thesis",
            parsed.thesis,
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_dry_receipt"
    assert payload["run_id"] == "pipeline_dry_receipt"
    assert (run_dir / "pipeline_run.log").is_file()
    assert (run_dir / "pipeline_runtime_config.json").is_file()
    assert (run_dir / "candidate_prefilter.json").is_file()
    runtime_receipt = json.loads((run_dir / "pipeline_runtime_config.json").read_text(encoding="utf-8"))
    assert runtime_receipt["pipeline_runtime_config_hash"]
    assert runtime_receipt["effective"]["max_candidates"] == 1
    receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
    assert receipt["run_id"] == "pipeline_dry_receipt"
    assert receipt["status"] == "dry_run_complete"
    assert receipt["candidate_prefilter"]["accepted_count"] == 1


def test_run_pipeline_failure_writes_receipt(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline

    request = {
        "schema_version": "1",
        "request_id": "pipeline_failure_receipt",
        "thesis": "Fade spread blowout after CPI",
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_failure_receipt")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic parse failure")

    monkeypatch.setattr(run_pipeline, "parse_hypothesis", boom)

    code = run_pipeline.main(
        [
            "--thesis",
            "Fade spread blowout after CPI",
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 1
    assert "synthetic parse failure" in capsys.readouterr().err
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_failure_receipt"
    receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "pipeline_failed"
    assert receipt["error"]["type"] == "RuntimeError"
    assert receipt["request_packet"]["request_id"] == "pipeline_failure_receipt"


def test_run_pipeline_url_doc_source_is_not_path_normalized(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="heuristic",
    )
    candidate = CandidateModel(
        candidate_id="cand_url_doc",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.05},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_url_doc",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }
    captured = {}
    url = "https://example.com/research-note.txt"

    def fake_ingest(source, **kwargs):
        captured["source"] = source
        return "doc summary", {"status": "disabled_url_cache"}

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_url_doc")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "ingest_document_with_cache", fake_ingest)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        },
    )

    code = run_pipeline.main(
        [
            "--thesis",
            parsed.thesis,
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--doc",
            url,
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 0
    _last_json_object(capsys.readouterr().out)
    assert captured["source"] == url


def test_run_pipeline_idea_set_requires_vectorbt_writes_receipt(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, ParsedHypothesis

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
    idea_packet = _idea_packet()
    idea_packet["ideas"] = [idea_packet["ideas"][1]]
    idea_packet["ideas"][0]["status"] = "queued_for_test"
    candidate = CandidateModel(
        candidate_id="cand_idea",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.15},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_idea_requires_vbt",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_idea_requires_vbt")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: idea_packet)
    monkeypatch.setattr(run_pipeline, "candidates_from_ideas", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "parsed_from_idea", lambda idea: parsed)

    code = run_pipeline.main(
        [
            "--thesis",
            parsed.thesis,
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--idea-set",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 1
    payload = _last_json_object(capsys.readouterr().out)
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_idea_requires_vbt"
    receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
    prefilter = json.loads((run_dir / "candidate_prefilter.json").read_text(encoding="utf-8"))
    assert payload["status"] == "blocked_idea_set_requires_vectorbt_prefilter"
    assert receipt["status"] == "blocked_idea_set_requires_vectorbt_prefilter"
    assert receipt["candidate_prefilter"]["accepted_count"] == 1
    assert prefilter["accepted_ids"] == ["cand_idea"]


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
                "feature_ids": ["BOOK_PRESSURE"],
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
                "feature_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
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
    assert candidates[0].strategy_params["signal_threshold"] == 0.9
    assert parsed_from_idea(by_id["idea_high"]).param_ranges == {"signal_threshold": [0.90, 0.95]}
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
            "feature_ids": ["BOOK_PRESSURE"],
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
    assert parsed.indicators == ["BOOK_PRESSURE"]
    assert parsed.feature_list == ["SPREAD_BLOWOUT_RECOMPRESSION"]
    generated = list(generate_candidates(parsed, max_candidates=6))
    assert {candidate.model_id for candidate in generated} == {"SPREAD_BLOWOUT_RECOMPRESSION"}

    candidates = candidates_from_ideas(packet, max_candidates=6)
    assert candidates == []
    assert packet["ideas"][0]["status"] == "static_reject"
    assert "feature_model_id_not_allowed" in packet["ideas"][0]["static_error_codes"]


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


@pytest.mark.parametrize("idea_set", [False, True])
def test_run_pipeline_vectorbt_only_promoted_exits_before_hftbacktest(
    tmp_path, monkeypatch, idea_set
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate, RejectedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="idea_set" if idea_set else "heuristic",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
        metadata={"idea_id": "idea_001"} if idea_set else {},
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
                "feature_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
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
                    candidate_id="hashed_trial_cand_vbt",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                    asset_class="CME",
                    symbol="MES",
                    timeframe="1m",
                    param_values={"signal_threshold": 0.1},
                    vectorbt_run_id="vbt_test",
                    vectorbt_results={
                        "base_candidate_id": "cand_vbt",
                        "base_candidate_metadata": dict(candidate.metadata),
                        "oos_expectancy": 1.0,
                        "num_trades": 3,
                    },
                    pass_reason="vectorbt_screen_passed_replay_not_eligible",
                )
            ],
            rejected=[
                RejectedCandidate(
                    candidate_id="cand_rejected",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    reject_reason="NEGATIVE_OOS_EXPECTANCY",
                    metric_values={"parameter_values": {"signal_threshold": 0.2}},
                )
            ],
            vectorbt_available=True,
            backend="vectorbt",
            run_id="vbt_test",
            total_candidates=7,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="numba",
            engine_parity_status="rust_unavailable_pilot_only",
            rust_engine_required_for_scope=False,
            rust_engine_available=False,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=7,
            trials_run=7,
            max_total_trials=7,
            candidate_ids=["cand_vbt", "cand_rejected"],
            candidate_reasons={
                "cand_vbt": "queued_for_vectorbt_screen",
                "cand_rejected": "NEGATIVE_OOS_EXPECTANCY",
            },
            stop_reasons=[],
        )

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_only")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: idea_packet)
    monkeypatch.setattr(run_pipeline, "candidates_from_ideas", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "parsed_from_idea", lambda idea: parsed)
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "evaluate_model",
        lambda *args, **kwargs: pytest.fail("vectorbt-only called HftBacktest evaluate"),
    )
    monkeypatch.setattr(
        run_pipeline,
        "deploy_best",
        lambda *args, **kwargs: pytest.fail("vectorbt-only called deploy_best"),
    )

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
    if idea_set:
        argv.append("--idea-set")
    monkeypatch.setattr(sys, "argv", argv)

    assert run_pipeline.main() == 0
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_vbt_only"
    response = json.loads((run_dir / "response_packet.json").read_text(encoding="utf-8"))
    vectorbt_filter = json.loads((run_dir / "vectorbt_filter.json").read_text(encoding="utf-8"))
    screening_artifact = json.loads((run_dir / "screening_artifact.json").read_text(encoding="utf-8"))

    assert response["candidates_tested"] == 7
    assert response["results"] == []
    assert response["selected_model_id"] is None
    assert vectorbt_filter["promoted_count"] == 1
    assert screening_artifact == vectorbt_filter
    assert vectorbt_filter["screening_backend"] == "vectorbt"
    assert vectorbt_filter["vectorbt_version"] == "1.0.0"
    assert vectorbt_filter["vectorbt_engine"] == "numba"
    assert vectorbt_filter["engine_parity_status"] == "rust_unavailable_pilot_only"
    assert vectorbt_filter["rust_engine_required_for_scope"] is False
    assert vectorbt_filter["rust_engine_available"] is False
    assert vectorbt_filter["parameter_space_id"] == "vbt_ps_test"
    assert vectorbt_filter["parameter_space_hash"] == "ps_hash_test"
    assert vectorbt_filter["max_trials"] == 7
    assert vectorbt_filter["trials_run"] == 7
    assert vectorbt_filter["run_budget_id"] == "vbt2_pilot"
    assert vectorbt_filter["max_total_trials"] == 7
    assert vectorbt_filter["candidate_ids"] == ["hashed_trial_cand_vbt", "cand_rejected"]
    assert vectorbt_filter["promoted_ids"] == ["hashed_trial_cand_vbt"]
    assert vectorbt_filter["rejected_ids"] == ["cand_rejected"]
    assert vectorbt_filter["promoted_reasons"] == {
        "hashed_trial_cand_vbt": "vectorbt_screen_passed_replay_not_eligible"
    }
    assert vectorbt_filter["rejected_reasons"] == {"cand_rejected": "NEGATIVE_OOS_EXPECTANCY"}
    assert vectorbt_filter["rejected"][0]["candidate_id"] == "cand_rejected"
    assert vectorbt_filter["rejected"][0]["rejection_reason_or_null"] == "NEGATIVE_OOS_EXPECTANCY"
    assert vectorbt_filter["stop_reasons"] == []
    assert vectorbt_filter["license_review"]
    assert vectorbt_filter["research_clock"]
    assert vectorbt_filter["no_lookahead_signal_shift_proof"]
    assert vectorbt_filter["screening_artifact_hash"]
    if idea_set:
        assert response["idea_summary"]["candidates_from_ideas"] == 1
        assert (run_dir / "idea_set_packet.json").is_file()
        idea_packet_out = json.loads((run_dir / "idea_set_packet.json").read_text(encoding="utf-8"))
        assert idea_packet_out["ideas"][0]["status"] == "queued_for_test"


@pytest.mark.parametrize("scope_alias", ["broad", "broad-screen", "paid-compute", "all-models"])
def test_run_pipeline_accepts_rust_required_vectorbt_scope_aliases(
    tmp_path, monkeypatch, scope_alias
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import RejectedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        source="heuristic",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
        metadata={},
    )
    captured = {}

    def fake_filter_candidates(*args, **kwargs):
        captured["screening_scope"] = kwargs["screening_scope"]
        captured["run_budget"] = kwargs["run_budget"]
        return FilterResult(
            rejected=[
                RejectedCandidate(
                    candidate_id="cand_rejected",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    reject_reason="rust_runtime_proof_missing_fail_closed",
                    metric_values={
                        "base_candidate_id": "cand_vbt",
                        "base_candidate_metadata": {},
                    },
                )
            ],
            vectorbt_available=True,
            backend="vectorbt_rust_unavailable",
            run_id="vbt_scope_alias",
            total_candidates=1,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="numba",
            engine_parity_status="rust_runtime_proof_missing_fail_closed",
            rust_engine_required_for_scope=True,
            rust_engine_available=True,
            vectorbt_engine_runtime_proof=False,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=0,
            max_total_trials=1,
            screening_scope=scope_alias.replace("-", "_"),
            stop_reasons=["rust_runtime_proof_missing_fail_closed"],
        )

    run_id = f"pipeline_{scope_alias.replace('-', '_')}"
    monkeypatch.setattr(run_pipeline, "_run_id", lambda: run_id)
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: {
        "schema_version": "1",
        "request_id": run_id,
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {
            "connector_id": "hft3-cme-mbo",
            "asset_class": "cme_mbo_microstructure",
            "vendor_shas": {"openfoundry": "test"},
            "schema_version": "1",
        },
        "max_candidates": 1,
    })
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "evaluate_model",
        lambda *args, **kwargs: pytest.fail("vectorbt-only called HftBacktest evaluate"),
    )
    monkeypatch.setattr(sys, "argv", [
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
        "--vectorbt-scope",
        scope_alias,
        "--vectorbt-max-trials",
        "11",
        "--vectorbt-max-models",
        "3",
        "--vectorbt-max-symbols",
        "2",
        "--vectorbt-max-feature-sets",
        "4",
        "--vectorbt-max-total-trials",
        "33",
        "--vectorbt-max-wall-clock-seconds",
        "120",
        "--vectorbt-max-peak-memory-mb",
        "2048",
    ])

    assert run_pipeline.main() == 1
    assert captured["screening_scope"] == scope_alias
    assert captured["run_budget"] == {
        "max_trials": 11,
        "max_models": 3,
        "max_symbols": 2,
        "max_feature_sets": 4,
        "max_total_trials": 33,
        "max_wall_clock_seconds": 120,
        "max_peak_memory_mb_or_null": 2048,
    }


def test_run_pipeline_vectorbt_full_requires_hftbacktest_opt_in(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate, RejectedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_vbt_full",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    def fake_filter_candidates(*args, **kwargs):
        assert kwargs["screening_scope"] == "pilot"
        return FilterResult(
            promoted=[
                PromotedCandidate(
                    candidate_id="hashed_trial_cand_vbt",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                    asset_class="CME",
                    symbol="MES",
                    timeframe="1m",
                    param_values={"signal_threshold": 0.1},
                    vectorbt_run_id="vbt_test",
                    vectorbt_results={
                        "base_candidate_id": "cand_vbt",
                        "base_candidate_metadata": dict(candidate.metadata),
                        "oos_expectancy": 1.0,
                        "num_trades": 3,
                    },
                    pass_reason="vectorbt_screen_passed_replay_not_eligible",
                )
            ],
            rejected=[
                RejectedCandidate(
                    candidate_id="cand_rejected",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    reject_reason="NEGATIVE_OOS_EXPECTANCY",
                    metric_values={"parameter_values": {"signal_threshold": 0.2}},
                )
            ],
            vectorbt_available=True,
            backend="vectorbt",
            run_id="vbt_test",
            total_candidates=2,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="numba",
            engine_parity_status="rust_unavailable_pilot_only",
            rust_engine_required_for_scope=False,
            rust_engine_available=False,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            candidate_ids=["cand_vbt", "cand_rejected"],
            candidate_reasons={
                "cand_vbt": "queued_for_vectorbt_screen",
                "cand_rejected": "NEGATIVE_OOS_EXPECTANCY",
            },
            stop_reasons=[],
        )

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_full")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "evaluate_model",
        lambda *args, **kwargs: pytest.fail("full vectorbt called Workbench evaluation before explicit HftBacktest opt-in"),
    )
    monkeypatch.setattr(
        run_pipeline,
        "write_hftbacktest_realism_artifacts",
        lambda *args, **kwargs: pytest.fail("default --vectorbt called HftBacktest writer without explicit opt-in"),
    )
    monkeypatch.setattr(sys, "argv", [
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
        "--vectorbt",
    ])

    assert run_pipeline.main() == 2
    payload = _last_json_object(capsys.readouterr().out)
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_vbt_full"
    vectorbt_filter = json.loads((run_dir / "vectorbt_filter.json").read_text(encoding="utf-8"))
    screening_artifact = json.loads((run_dir / "screening_artifact.json").read_text(encoding="utf-8"))

    assert payload["status"] == "blocked_downstream_realism_opt_in_required"
    assert "required HftBacktest input artifacts" in payload["detail"]
    assert screening_artifact == vectorbt_filter
    assert vectorbt_filter["promoted_ids"] == ["hashed_trial_cand_vbt"]
    assert vectorbt_filter["rejected_ids"] == ["cand_rejected"]
    assert not (run_dir / "response_packet.json").exists()


def test_run_pipeline_vectorbt_hftbacktest_opt_in_calls_writer(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_vbt_hbt",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    def fake_filter_candidates(*args, **kwargs):
        return FilterResult(
            promoted=[
                PromotedCandidate(
                    candidate_id="hashed_trial_cand_vbt",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                    asset_class="CME",
                    symbol="MES",
                    timeframe="1m",
                    param_values={"signal_threshold": 0.1},
                    vectorbt_run_id="vbt_test",
                    vectorbt_results={
                        "base_candidate_id": "cand_vbt",
                        "base_candidate_metadata": dict(candidate.metadata),
                        "oos_expectancy": 1.0,
                        "num_trades": 12,
                    },
                    pass_reason="vectorbt_screen_passed_replay_not_eligible",
                )
            ],
            rejected=[],
            vectorbt_available=True,
            backend="vectorbt",
            run_id="vbt_test",
            total_candidates=1,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="rust",
            engine_parity_status="rust_engine_verified",
            rust_engine_required_for_scope=False,
            rust_engine_available=True,
            vectorbt_engine_runtime_proof=True,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            candidate_ids=["cand_vbt"],
            candidate_reasons={"cand_vbt": "queued_for_vectorbt_screen"},
            stop_reasons=[],
        )

    data_path = tmp_path / "data.npz"
    latency_path = tmp_path / "latency.json"
    fill_queue_path = tmp_path / "fill_queue.json"
    observation_path = tmp_path / "observation.json"
    for path in (data_path, latency_path, fill_queue_path, observation_path):
        path.write_text("{}", encoding="utf-8")
    captured = {}

    def fake_writer(**kwargs):
        captured.update(kwargs)
        return {
            "replay_summary": {
                "run_id": "pipeline_vbt_hbt",
                "replay_realism_status": "pass",
                "fail_closed_reasons": [],
            },
            "source_lock_path": str(kwargs["out_dir"] / "hftbacktest_source_lock.json"),
            "latency_model_path": str(kwargs["out_dir"] / "latency_model.json"),
            "fill_queue_model_path": str(kwargs["out_dir"] / "fill_queue_model.json"),
            "official_replay_path": str(kwargs["out_dir"] / "official_replay.json"),
            "replay_summary_path": str(kwargs["out_dir"] / "replay_summary.json"),
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_hbt")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "_strict_replay_eligible_ids",
        lambda artifact: (["hashed_trial_cand_vbt"], {}),
    )
    monkeypatch.setattr(run_pipeline, "write_hftbacktest_realism_artifacts", fake_writer)
    monkeypatch.setattr(sys, "argv", [
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
        "--vectorbt",
        "--hftbacktest-realism",
        "--hftbacktest-data-npz",
        str(data_path),
        "--hftbacktest-latency-model",
        str(latency_path),
        "--hftbacktest-fill-queue-model",
        str(fill_queue_path),
        "--hftbacktest-observation-artifact",
        str(observation_path),
        "--hftbacktest-candidate-id",
        "hashed_trial_cand_vbt",
        "--hftbacktest-upstream-ref",
        "v2.4.2",
        "--native-hot-path-evidence",
        "sha256:native-hot-path",
    ])

    assert run_pipeline.main() == 0
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_vbt_hbt"
    payload = _last_json_object(capsys.readouterr().out)

    assert captured["repo_root"] == tmp_path.resolve()
    assert captured["out_dir"] == run_dir / "hftbacktest_realism"
    assert captured["screening_artifact_path"] == run_dir / "screening_artifact.json"
    assert captured["data_npz_path"] == data_path.resolve()
    assert captured["latency_model_path"] == latency_path.resolve()
    assert captured["fill_queue_model_path"] == fill_queue_path.resolve()
    assert captured["observation_artifact_path"] == observation_path.resolve()
    assert captured["candidate_id"] == "hashed_trial_cand_vbt"
    assert captured["upstream_ref"] == "v2.4.2"
    assert captured["native_hot_path_evidence"] == ["sha256:native-hot-path"]
    assert captured["run_id"] == "pipeline_vbt_hbt"
    assert payload["status"] == "hftbacktest_realism_pass"
    assert payload["replay_summary"]["replay_realism_status"] == "pass"
    assert payload["hftbacktest_realism"]["replay_summary"] == payload["replay_summary"]
    assert payload["paths"]["screening_artifact_path"] == str(run_dir / "screening_artifact.json")
    assert payload["paths"]["hftbacktest_realism_dir"] == str(run_dir / "hftbacktest_realism")


def test_run_pipeline_vectorbt_hftbacktest_opt_in_blocks_without_strict_replay_eligibility(
    tmp_path, monkeypatch, capsys
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import PromotedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
    )

    def fake_filter_candidates(*args, **kwargs):
        return FilterResult(
            promoted=[
                PromotedCandidate(
                    candidate_id="hashed_trial_cand_vbt",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    strategy_family="SPREAD_BLOWOUT_RECOMPRESSION",
                    asset_class="CME",
                    symbol="MES",
                    timeframe="1m",
                    param_values={"signal_threshold": 0.1},
                    vectorbt_run_id="vbt_test",
                    vectorbt_results={
                        "base_candidate_id": "cand_vbt",
                        "oos_expectancy": 1.0,
                        "num_trades": 12,
                    },
                    pass_reason="vectorbt_screen_passed_replay_not_eligible",
                )
            ],
            rejected=[],
            vectorbt_available=True,
            backend="vectorbt",
            run_id="vbt_test",
            total_candidates=1,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="rust",
            engine_parity_status="rust_engine_verified",
            rust_engine_required_for_scope=False,
            rust_engine_available=True,
            vectorbt_engine_runtime_proof=True,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            candidate_ids=["cand_vbt"],
            candidate_reasons={"cand_vbt": "queued_for_vectorbt_screen"},
            stop_reasons=[],
        )

    data_path = tmp_path / "data.npz"
    latency_path = tmp_path / "latency.json"
    fill_queue_path = tmp_path / "fill_queue.json"
    for path in (data_path, latency_path, fill_queue_path):
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_hbt_ineligible")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: {
        "schema_version": "1",
        "request_id": "pipeline_vbt_hbt_ineligible",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    })
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "write_hftbacktest_realism_artifacts",
        lambda *args, **kwargs: pytest.fail("ineligible row called HftBacktest writer"),
    )
    monkeypatch.setattr(sys, "argv", [
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
        "--vectorbt",
        "--hftbacktest-realism",
        "--hftbacktest-data-npz",
        str(data_path),
        "--hftbacktest-latency-model",
        str(latency_path),
        "--hftbacktest-fill-queue-model",
        str(fill_queue_path),
    ])

    assert run_pipeline.main() == 2
    payload = _last_json_object(capsys.readouterr().out)

    assert payload["status"] == "blocked_hftbacktest_realism_replay_ineligible"
    assert payload["hftbacktest_realism"] is None
    assert payload["replay_summary"]["fail_closed_reasons"] == [
        "screening_artifact_has_no_strict_replay_eligible_candidate"
    ]


def test_run_pipeline_vectorbt_hftbacktest_opt_in_no_promoted_does_not_call_writer(
    tmp_path, monkeypatch, capsys
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.promotion_gate import RejectedCandidate
    from backtest_pipeline.src.vectorbt_adapter import FilterResult
    from research_pipeline.types import CandidateModel, ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="Fade spread blowout after CPI",
        instrument_universe=["MES"],
        entry_rules=["enter_spread"],
        exit_rules=["exit_revert"],
        indicators=["SPREAD_BLOWOUT_RECOMPRESSION"],
        feature_list=["SPREAD_BLOWOUT_RECOMPRESSION"],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    candidate = CandidateModel(
        candidate_id="cand_vbt",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_vbt_hbt_no_promoted",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    def fake_filter_candidates(*args, **kwargs):
        return FilterResult(
            promoted=[],
            rejected=[
                RejectedCandidate(
                    candidate_id="cand_rejected",
                    hypothesis_id="SPREAD_BLOWOUT_RECOMPRESSION",
                    reject_reason="NEGATIVE_OOS_EXPECTANCY",
                    metric_values={"parameter_values": {"signal_threshold": 0.2}},
                )
            ],
            vectorbt_available=True,
            backend="vectorbt",
            run_id="vbt_test",
            total_candidates=1,
            code_commit="abc123",
            vectorbt_version="1.0.0",
            vectorbt_engine="rust",
            engine_parity_status="rust_engine_verified",
            rust_engine_required_for_scope=False,
            rust_engine_available=True,
            vectorbt_engine_runtime_proof=True,
            parameter_space_id="vbt_ps_test",
            parameter_space_hash="ps_hash_test",
            max_trials=1,
            trials_run=1,
            max_total_trials=1,
            candidate_ids=["cand_vbt"],
            candidate_reasons={"cand_vbt": "queued_for_vectorbt_screen"},
            stop_reasons=[],
        )

    data_path = tmp_path / "data.npz"
    latency_path = tmp_path / "latency.json"
    fill_queue_path = tmp_path / "fill_queue.json"
    for path in (data_path, latency_path, fill_queue_path):
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_vbt_hbt_no_promoted")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "filter_candidates", fake_filter_candidates)
    monkeypatch.setattr(
        run_pipeline,
        "write_hftbacktest_realism_artifacts",
        lambda *args, **kwargs: pytest.fail("no-promoted HftBacktest opt-in called writer"),
    )
    monkeypatch.setattr(sys, "argv", [
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
        "--vectorbt",
        "--hftbacktest-realism",
        "--hftbacktest-data-npz",
        str(data_path),
        "--hftbacktest-latency-model",
        str(latency_path),
        "--hftbacktest-fill-queue-model",
        str(fill_queue_path),
    ])

    assert run_pipeline.main() == 2
    payload = _last_json_object(capsys.readouterr().out)

    assert payload["status"] == "blocked_hftbacktest_realism_no_promoted_candidates"
    assert payload["hftbacktest_realism"] is None
    assert payload["replay_summary"]["replay_realism_status"] == "fail"
    assert payload["replay_summary"]["fail_closed_reasons"] == [
        "screening_artifact_has_no_promoted_candidate"
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
                    "feature_ids": ["SPREAD_BLOWOUT_RECOMPRESSION"],
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
