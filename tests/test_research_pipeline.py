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
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    return payload


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


@pytest.mark.parametrize(
    ("thesis", "expected_symbol"),
    [
        ("trade micro NQ futures after CPI", "MNQ"),
        ("GOLD breakout after claims", "GC"),
        ("WTI liquidity vacuum after EIA", "CL"),
        ("10Y queue imbalance during macro shock", "ZN"),
    ],
)
def test_parse_hypothesis_detects_symbol_aliases(thesis, expected_symbol):
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(thesis, use_llm=False)

    assert expected_symbol in parsed.instrument_universe


def test_parse_hypothesis_ignores_duration_phrases_as_treasury_aliases():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(
        "Use the continuation model over the last 10-year backtest window",
        use_llm=False,
    )

    assert parsed.primary_model_id == "SECOND_WAVE_CONTINUATION"
    assert parsed.instrument_universe == ["MES"]
    assert parsed.metadata["instrument_universe_compatibility"] == "compatible"


def test_symbol_aliases_do_not_include_duration_year_phrases():
    from research_pipeline import hypothesis_parser

    aliases = hypothesis_parser._symbol_aliases()

    for values in aliases.values():
        normalised = [hypothesis_parser._normalize_alias_text(alias) for alias in values]
        assert not any(value.endswith(" YEAR") for value in normalised)


def test_parse_hypothesis_does_not_seed_mes_when_other_symbol_present():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("GOLD breakout after claims", use_llm=False)

    assert parsed.instrument_universe == ["GC"]
    assert "MES" not in parsed.instrument_universe


def test_parse_hypothesis_prefers_longest_symbol_alias():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("trade micro NQ futures and micro GOLD after CPI", use_llm=False)

    assert "MNQ" in parsed.instrument_universe
    assert "MGC" in parsed.instrument_universe
    assert "NQ" not in parsed.instrument_universe
    assert "GC" not in parsed.instrument_universe


def test_symbol_aliases_reflect_path_changes(tmp_path, monkeypatch):
    from research_pipeline import hypothesis_parser

    first = tmp_path / "first_aliases.yaml"
    second = tmp_path / "second_aliases.yaml"
    first.write_text("MES:\n  - micro test symbol\n", encoding="utf-8")
    second.write_text("MNQ:\n  - micro test symbol\n", encoding="utf-8")

    try:
        monkeypatch.setattr(hypothesis_parser, "_SYMBOL_ALIASES_PATH", first)
        hypothesis_parser._symbol_aliases.cache_clear()
        assert hypothesis_parser.canonicalize_instrument("micro test symbol") == "MES"

        monkeypatch.setattr(hypothesis_parser, "_SYMBOL_ALIASES_PATH", second)
        hypothesis_parser._symbol_aliases.cache_clear()
        assert hypothesis_parser.canonicalize_instrument("micro test symbol") == "MNQ"
    finally:
        hypothesis_parser._symbol_aliases.cache_clear()


def test_symbol_aliases_are_cached_per_process(tmp_path, monkeypatch):
    from research_pipeline import hypothesis_parser

    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("MES:\n  - micro test symbol\n", encoding="utf-8")

    try:
        monkeypatch.setattr(hypothesis_parser, "_SYMBOL_ALIASES_PATH", aliases)
        hypothesis_parser._symbol_aliases.cache_clear()
        assert hypothesis_parser.canonicalize_instrument("micro test symbol") == "MES"

        aliases.write_text("MNQ:\n  - micro test symbol\n", encoding="utf-8")
        assert hypothesis_parser.canonicalize_instrument("micro test symbol") == "MES"
    finally:
        hypothesis_parser._symbol_aliases.cache_clear()


def test_symbol_aliases_do_not_cache_absent_file(tmp_path, monkeypatch):
    from research_pipeline import hypothesis_parser

    aliases = tmp_path / "aliases.yaml"

    try:
        monkeypatch.setattr(hypothesis_parser, "_SYMBOL_ALIASES_PATH", aliases)
        hypothesis_parser._symbol_aliases.cache_clear()
        assert hypothesis_parser._symbol_aliases() == {}

        aliases.write_text("MES:\n  - micro test symbol\n", encoding="utf-8")
        assert hypothesis_parser.canonicalize_instrument("micro test symbol") == "MES"
    finally:
        hypothesis_parser._symbol_aliases.cache_clear()


def test_model_alias_matching_requires_word_boundary(monkeypatch):
    from research_pipeline import hypothesis_parser

    monkeypatch.setattr(
        hypothesis_parser,
        "load_model_registry",
        lambda: {
            "models": {
                "ES": {
                    "kind": "hypothesis",
                    "display_name": "ES",
                    "aliases": ["es"],
                }
            }
        },
    )

    assert hypothesis_parser._match_model("best process estimate") == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert hypothesis_parser._match_model("trade ES after CPI") == "ES"


def test_parse_hypothesis_uses_model_alias_and_registry_ranges():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Run a blowout fade on GOLD after CPI", use_llm=False)

    assert parsed.primary_model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert parsed.param_ranges["signal_threshold"] == [0.02, 0.15]
    assert parsed.param_ranges["stop_loss"] == [0.05, 0.30]
    assert parsed.metadata["volatility_regime"] == "high_volatility"
    assert parsed.metadata["instrument_universe_compatibility"] == "unsupported_instruments"
    assert parsed.metadata["unsupported_instruments"] == ["GC"]


def test_parse_hypothesis_packet_accepts_enriched_fields(monkeypatch):
    import sys
    import types

    from research_pipeline.hypothesis_parser import parse_hypothesis

    request = {"request_id": "req_parse_enriched"}

    packet_runner = types.ModuleType("data_layer.llm.packet_runner")
    packet_runner.run_llm_on_hypothesis_request = (
        lambda *a, **k: {
            "llm_status": "ok",
            "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "target_instruments": ["GOLD"],
            "entry_rule": "fade spread when book imbalance normalizes",
            "exit_rule": "exit after recompression",
            "indicative_stop_loss": 0.12,
            "expected_holding_period": 5,
        }
    )
    data_layer = types.ModuleType("data_layer")
    llm_pkg = types.ModuleType("data_layer.llm")
    monkeypatch.setitem(sys.modules, "data_layer", data_layer)
    monkeypatch.setitem(sys.modules, "data_layer.llm", llm_pkg)
    monkeypatch.setitem(sys.modules, "data_layer.llm.packet_runner", packet_runner)

    parsed = parse_hypothesis(
        "Fade GOLD blowout after CPI",
        pipeline_request=request,
        repo_root=REPO,
    )

    assert parsed.source == "hypothesis_packet"
    assert parsed.instrument_universe == ["GC"]
    assert parsed.entry_rules == ["fade spread when book imbalance normalizes"]
    assert parsed.exit_rules == ["exit after recompression"]
    assert parsed.param_ranges["signal_threshold"] == [0.02, 0.15]
    assert parsed.metadata["indicative_stop_loss"] == 0.12
    assert parsed.metadata["expected_holding_period"] == 5
    assert parsed.metadata["instrument_universe_compatibility"] == "unsupported_instruments"


def test_generate_candidates_respects_max():
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.model_generation import generate_candidates

    parsed = parse_hypothesis("spread recompression", use_llm=False)
    cands = list(generate_candidates(parsed, max_candidates=2))
    assert len(cands) == 2
    assert cands[0].strategy_params != cands[1].strategy_params
    assert "stop_loss_pct" in cands[0].strategy_params
    assert "take_profit_pct" in cands[0].strategy_params


def test_parameter_search_grid_and_vectorbt_ranges_are_deterministic():
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.parameter_search import parameter_grid, select_parameters

    parsed = parse_hypothesis("Run a blowout fade on MES after CPI", use_llm=False)
    base_grid = parameter_grid(parsed)
    grid = parameter_grid(parsed, expand_for_vectorbt=True)
    selected = select_parameters(grid, max_candidates=4, search_method="grid")

    assert "holding_period_bars" not in base_grid
    assert base_grid["stop_loss_pct"] == [0.05, 0.175, 0.3]
    assert base_grid["take_profit_pct"] == [0.05, 0.175, 0.3]
    assert grid["signal_threshold"] == [0.02, 0.085, 0.15]
    assert grid["holding_period_bars"] == [1, 6, 10]
    assert grid["stop_loss_pct"] == [0.05, 0.175, 0.3]
    assert grid["take_profit_pct"] == [0.05, 0.175, 0.3]
    assert [item.params for item in selected] == [item.params for item in select_parameters(grid, max_candidates=4)]
    assert selected[0].metadata["method_status"] == "ok"
    assert selected[0].metadata["grid_size"] == 81
    assert all(value is not None for item in selected for value in item.params.values())


def test_parameter_grid_skips_optional_risk_params_without_ranges():
    from research_pipeline.parameter_search import parameter_grid, select_parameters
    from research_pipeline.types import ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="signal only",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={"signal_threshold": [0.1, 0.2]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )

    grid = parameter_grid(parsed)
    selected = select_parameters(grid, max_candidates=2)

    assert grid["stop_loss_pct"] == []
    assert grid["take_profit_pct"] == []
    assert all("stop_loss_pct" not in item.params for item in selected)
    assert all("take_profit_pct" not in item.params for item in selected)


def test_parameter_search_seeded_and_unavailable_methods_are_explicit():
    from research_pipeline.hypothesis_parser import parse_hypothesis
    from research_pipeline.parameter_search import parameter_grid, select_parameters

    parsed = parse_hypothesis("Run a blowout fade on MES after CPI", use_llm=False)
    grid = parameter_grid(parsed, expand_for_vectorbt=True)
    seeded_a = select_parameters(grid, max_candidates=3, search_method="seeded", seed=9)
    seeded_b = select_parameters(grid, max_candidates=3, search_method="seeded", seed=9)
    fallback = select_parameters(grid, max_candidates=3, search_method="bayesian", seed=9)

    assert [item.params for item in seeded_a] == [item.params for item in seeded_b]
    assert fallback[0].metadata["method_status"] == "method_unavailable"
    assert fallback[0].metadata["fallback_method"] == "seeded"
    assert [item.params for item in fallback] == [item.params for item in seeded_a]


def test_parameter_search_rejects_alias_key_collisions():
    from research_pipeline.parameter_search import select_parameters

    with pytest.raises(ValueError, match="duplicate parameter range"):
        select_parameters(
            {"stop_loss": [0.01], "stop_loss_pct": [0.02]},
            max_candidates=1,
        )


def test_parameter_grid_rejects_parsed_alias_key_collisions():
    from research_pipeline.parameter_search import parameter_grid
    from research_pipeline.types import ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="duplicate parsed ranges",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={"stop_loss_pct": [0.01], "stop_loss": [0.02]},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )

    with pytest.raises(ValueError, match="duplicate parameter range"):
        parameter_grid(parsed)


def test_model_ids_for_search_ignores_uppercase_non_registry_features():
    from research_pipeline.parameter_search import model_ids_for_search
    from research_pipeline.types import ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="hybrid uppercase feature filter",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["OFI", "VAMP", "SECOND_WAVE_CONTINUATION", "CPI"],
        param_ranges={},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )

    assert model_ids_for_search(parsed, hybrid=True) == [
        "SPREAD_BLOWOUT_RECOMPRESSION",
        "SECOND_WAVE_CONTINUATION",
    ]


def test_generate_candidates_records_search_metadata_and_hybrid_limit():
    from research_pipeline.types import ParsedHypothesis
    from research_pipeline.model_generation import generate_candidates

    parsed = ParsedHypothesis(
        thesis="hybrid test",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["SECOND_WAVE_CONTINUATION", "STOP_RUN_EXHAUSTION_FADE"],
        param_ranges={
            "signal_threshold": [0.1, 0.3],
            "stop_loss_pct": [0.01, 0.03],
        },
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )

    cands = list(generate_candidates(parsed, max_candidates=5, hybrid=True, search_method="hybrid", search_seed=3))

    assert len(cands) == 5
    model_ids = {cand.model_id for cand in cands}
    params_seen = {tuple(sorted(cand.strategy_params.items())) for cand in cands}
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in model_ids
    assert model_ids & {"SECOND_WAVE_CONTINUATION", "STOP_RUN_EXHAUSTION_FADE"}
    assert len(params_seen) == len(cands)
    assert sum(cand.model_id == "SPREAD_BLOWOUT_RECOMPRESSION" for cand in cands) >= 3
    assert all(cand.metadata["parameter_search"]["search_method"] == "hybrid" for cand in cands)
    assert cands[0].metadata["parameter_search"]["max_candidates"] == 5


def test_run_pipeline_dry_run_exposes_search_metadata(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_search")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
        "--no-llm",
        "--max-candidates",
        "2",
        "--search-method",
        "bayesian",
        "--search-seed",
        "9",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)

    search_meta = payload["candidates"][0]["metadata"]["parameter_search"]
    assert search_meta["method_status"] == "method_unavailable"
    assert search_meta["fallback_method"] == "seeded"


def test_run_pipeline_rejects_candidate_generation_value_error_cleanly(
    tmp_path,
    monkeypatch,
    capsys,
):
    import sys

    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    parsed = parse_hypothesis("Run a blowout fade on MES after CPI", use_llm=False)
    parsed.param_ranges["stop_loss_pct"] = [0.01, 0.02]

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_candidate_error")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
        "--no-llm",
    ])

    assert run_pipeline.main() == 2
    captured = capsys.readouterr()
    assert "duplicate parameter range for 'stop_loss_pct'" in captured.err
    assert "Traceback" not in captured.err


def test_parse_event_ids_supports_repeated_and_comma_separated_values():
    from research_pipeline.evaluation import parse_event_ids

    assert parse_event_ids(["CPI_1,NFP_1", "CPI_1", "FOMC_1"]) == ["CPI_1", "NFP_1", "FOMC_1"]


def test_aggregate_evaluation_results_applies_risk_gates():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="cand_cross",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis="cross-event",
    )
    permissive = GateThresholds(min_trades=0)
    strict = GateThresholds(min_trades=0, max_drawdown=4.0)
    per_event = [
        EvaluationResult(candidate, "NFP_2024_10_04", -5.0, 5, 0.4, -1.0, -5.0, permissive),
        EvaluationResult(candidate, "CPI_2024_09_11_TIGHT", 10.0, 5, 0.6, 2.0, -1.0, permissive),
        EvaluationResult(candidate, "FOMC_2024_11_07", 20.0, 10, 0.7, 2.0, -0.5, permissive),
    ]

    aggregate = aggregate_evaluation_results(candidate, per_event, gates=strict)

    assert aggregate.event_id == "CPI_2024_09_11_TIGHT,NFP_2024_10_04,FOMC_2024_11_07"
    assert aggregate.net_pnl == 25.0
    assert aggregate.num_trades == 20
    assert aggregate.win_rate == pytest.approx(0.6)
    assert aggregate.tail_loss == -5.0
    assert aggregate.max_drawdown == 5.0
    assert aggregate.risk_metrics_source == "cross_event_net_pnl_chronological"
    assert aggregate.risk_metrics_gateable is True
    assert aggregate.event_results[1]["event_id"] == "NFP_2024_10_04"
    assert aggregate.passes_all_gates() is False


def test_sortino_single_downside_event_does_not_bypass_gate():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="cand_sortino_single_downside",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis="cross-event",
    )
    gates = GateThresholds(min_trades=0, min_sortino=1e8)
    per_event = [
        EvaluationResult(candidate, "CPI_2024_09_11_TIGHT", 10.0, 5, 0.6, 2.0, -1.0, gates),
        EvaluationResult(candidate, "NFP_2024_10_04", -1.0, 5, 0.4, -0.2, -1.0, gates),
        EvaluationResult(candidate, "FOMC_2024_11_07", 5.0, 5, 0.6, 1.0, -0.5, gates),
    ]

    aggregate = aggregate_evaluation_results(candidate, per_event, gates=gates)

    assert aggregate.sortino < 1e8
    assert aggregate.passes_all_gates() is False


def test_event_payload_passes_uses_basic_gates_under_risk_gates():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="cand_event_payload_passes",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis="cross-event",
    )
    gates = GateThresholds(min_trades=1, min_sortino=0.0)
    per_event = [
        EvaluationResult(candidate, "CPI_2024_09_11_TIGHT", 10.0, 5, 0.6, 2.0, -1.0, gates),
        EvaluationResult(candidate, "NFP_2024_10_04", 5.0, 5, 0.6, 1.0, -1.0, gates),
    ]

    aggregate = aggregate_evaluation_results(candidate, per_event, gates=gates)

    assert [payload["passes"] for payload in aggregate.event_results] == [True, True]
    assert aggregate.passes_all_gates() is True


def test_cross_event_risk_metrics_fail_closed_without_dated_event_order():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="cand_cross_undated",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis="cross-event",
    )
    permissive = GateThresholds(min_trades=0)
    per_event = [
        EvaluationResult(candidate, "NFP_1", -5.0, 5, 0.4, -1.0, -5.0, permissive),
        EvaluationResult(candidate, "CPI_1", 10.0, 5, 0.6, 2.0, -1.0, permissive),
    ]

    aggregate = aggregate_evaluation_results(
        candidate,
        per_event,
        gates=GateThresholds(min_trades=0, max_drawdown=4.0),
    )

    assert aggregate.risk_metrics_source == "cross_event_net_pnl_diagnostic"
    assert aggregate.risk_metrics_gateable is False
    assert aggregate.passes_all_gates() is False


def test_cross_event_risk_metrics_fail_closed_on_same_date_ties():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="cand_cross_same_date",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.1},
        thesis="cross-event",
    )
    permissive = GateThresholds(min_trades=0)
    per_event = [
        EvaluationResult(candidate, "CPI_2024_09_11_TIGHT", 10.0, 5, 0.6, 2.0, -1.0, permissive),
        EvaluationResult(candidate, "EIA_2024_09_11", -5.0, 5, 0.4, -1.0, -5.0, permissive),
    ]

    aggregate = aggregate_evaluation_results(
        candidate,
        per_event,
        gates=GateThresholds(min_trades=0, max_drawdown=4.0),
    )

    assert aggregate.risk_metrics_source == "cross_event_net_pnl_diagnostic"
    assert aggregate.risk_metrics_gateable is False
    assert aggregate.passes_all_gates() is False


def test_cross_event_tail_loss_threshold_requires_gateable_metrics():
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    result = EvaluationResult(
        candidate=CandidateModel(
            candidate_id="cand_tail",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={},
            thesis="tail gate",
        ),
        event_id="CPI_1,NFP_1",
        net_pnl=10.0,
        num_trades=5,
        win_rate=1.0,
        expectancy=2.0,
        tail_loss=-5.0,
        gates=GateThresholds(min_trades=0, max_tail_loss=-2.0),
        risk_metrics_gateable=False,
    )

    assert result.passes_all_gates() is False


def test_tail_loss_gate_uses_signed_tail_pnl_floor():
    from research_pipeline.types import GateThresholds

    gates = GateThresholds(min_trades=0, max_tail_loss=-2.0)

    assert gates.passes(1.0, 1, -1.0, 1.0)
    assert not gates.passes(1.0, 1, -5.0, 1.0)


def test_tail_loss_gate_accepts_legacy_positive_loss_magnitude_cap():
    from research_pipeline.types import GateThresholds

    gates = GateThresholds(min_trades=0, max_tail_loss=2.0)

    assert gates.signed_tail_loss_floor() == -2.0
    assert gates.requires_gateable_risk_metrics() is True
    assert gates.passes(1.0, 1, -1.0, 1.0)
    assert not gates.passes(1.0, 1, -5.0, 1.0)
    assert GateThresholds(min_trades=0, max_tail_loss=1e9).requires_gateable_risk_metrics() is False


def test_sortino_no_downside_positive_mean_uses_large_sentinel():
    from research_pipeline.evaluation import _max_drawdown, _sharpe, _sortino

    assert _sharpe([2.0, 2.0, 2.0]) == 1e9
    assert _sharpe([-2.0, -2.0, -2.0]) == -1e9
    assert _sharpe([0.0, 0.0]) == 0.0
    assert _sharpe([2.0]) == 0.0
    assert _sortino([1.0, 2.0, 3.0]) == 1e9
    assert _sortino([0.0, 0.0]) == 0.0
    assert _sortino([-1.0, 3.0, 3.0]) == pytest.approx(5.0 / 3.0)
    assert _sortino([-3.0, 1.0]) == pytest.approx(-1.0 / 3.0)
    assert _sortino([-1.0, 1.0]) == 0.0
    assert _max_drawdown([-10.0, 5.0]) == 10.0
    assert _max_drawdown([-5.0, -3.0]) == 8.0


def test_run_pipeline_passes_multi_event_set_to_evaluator(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import EvaluationResult, GateThresholds

    captured = {}

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    def fake_eval(candidate, event_ids, repo_root, **kwargs):
        captured["event_ids"] = list(event_ids)
        captured["gates"] = kwargs.get("gates")
        return EvaluationResult(
            candidate=candidate,
            event_id=",".join(event_ids),
            net_pnl=1.0,
            num_trades=1,
            win_rate=1.0,
            expectancy=1.0,
            tail_loss=0.0,
            gates=kwargs.get("gates") or GateThresholds(),
            sharpe=0.5,
            sortino=0.5,
            max_drawdown=0.0,
        )

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_cross_event")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "evaluate_candidate_events", fake_eval)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_1,NFP_1",
        "--event-id",
        "FOMC_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--max-candidates",
        "1",
        "--min-sharpe",
        "0.25",
        "--min-sortino",
        "0.50",
        "--max-drawdown",
        "4.0",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)

    assert captured["event_ids"] == ["CPI_1", "NFP_1", "FOMC_1"]
    assert captured["gates"].min_sharpe == 0.25
    assert captured["gates"].min_sortino == 0.50
    assert captured["gates"].max_drawdown == 4.0
    assert payload["report"]["event_id"] == "CPI_1"
    assert payload["report"]["event_ids"] == ["CPI_1", "NFP_1", "FOMC_1"]
    assert payload["response_packet"]["event_id"] == "CPI_1"
    assert payload["response_packet"]["event_ids"] == ["CPI_1", "NFP_1", "FOMC_1"]
    assert payload["response_packet"]["results"][0]["sharpe"] == 0.5


def test_run_pipeline_rejects_nonfinite_risk_gate_arg(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--min-sharpe",
        "nan",
    ])

    with pytest.raises(SystemExit) as excinfo:
        run_pipeline.main()

    assert excinfo.value.code == 2
    assert "must be a finite number" in capsys.readouterr().err


def test_run_pipeline_rejects_multi_event_vectorbt(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_1,NFP_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--vectorbt",
    ])

    assert run_pipeline.main() == 2
    assert "multi-event VectorBT/HftBacktest screening is not implemented" in capsys.readouterr().err


def test_run_pipeline_rejects_multi_event_autoresearch(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Run a blowout fade on MES after CPI",
        "--event-id",
        "CPI_1,NFP_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--autoresearch",
    ])

    assert run_pipeline.main() == 2
    assert "--autoresearch accepts exactly one event id" in capsys.readouterr().err


def test_run_pipeline_rejects_cli_symbol_mismatch(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_symbol_mismatch")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Trade micro NQ futures after CPI",
        "--event-id",
        "CPI_1",
        "--symbol",
        "MES",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
    ])

    assert run_pipeline.main() == 2
    assert "--symbol MES is not compatible" in capsys.readouterr().err


def test_run_pipeline_derives_target_symbol_from_parsed_instrument(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_symbol_derived")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Trade micro NQ futures after CPI",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["candidates"][0]["target_symbol"] == "MNQ"


def test_run_pipeline_rejects_unsupported_parsed_symbol(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_symbol_unsupported")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI on GOLD",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
    ])

    assert run_pipeline.main() == 2
    assert "not compatible with model SPREAD_BLOWOUT_RECOMPRESSION" in capsys.readouterr().err


def test_model_registry_declares_valid_instrument_universe_for_all_models():
    from features_engine.src.model_registry import load_model_registry

    models = load_model_registry()["models"]
    missing = [
        slug
        for slug, entry in models.items()
        if not entry.get("valid_instrument_universe")
    ]

    assert missing == []


def test_cross_asset_registry_universes_are_pair_scoped():
    from features_engine.src.model_registry import load_model_registry

    models = load_model_registry()["models"]

    assert models["ES_MES_LEAD_LAG"]["valid_instrument_universe"] == ["ES", "MES"]
    assert models["ES_MES_LEAD_LAG"]["target_instrument_universe"] == ["MES"]
    assert models["NQ_MNQ_LEAD_LAG"]["valid_instrument_universe"] == ["NQ", "MNQ"]
    assert models["NQ_MNQ_LEAD_LAG"]["target_instrument_universe"] == ["MNQ"]
    assert models["ES_NQ_DIVERGENCE_SNAPBACK"]["valid_instrument_universe"] == [
        "ES",
        "MES",
        "NQ",
        "MNQ",
    ]
    assert models["ZN_ZB_ES_NQ_MACRO_IMPULSE"]["valid_instrument_universe"] == [
        "ZN",
        "ZB",
        "ES",
        "MES",
        "NQ",
        "MNQ",
    ]
    assert models["ZN_ZB_ES_NQ_MACRO_IMPULSE"]["target_instrument_universe"] == [
        "ES",
        "MES",
        "NQ",
        "MNQ",
    ]
    assert models["MICRO_CONTRACT_RETAIL_LAG"]["valid_instrument_universe"] == ["ES", "MES"]
    assert models["MICRO_CONTRACT_RETAIL_LAG"]["target_instrument_universe"] == ["MES"]


def test_run_pipeline_resolves_completed_registry_instrument_universe():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Aggressor deceleration fade on MES after CPI", use_llm=False)

    assert parsed.primary_model_id == "AGGRESSOR_DECELERATION_FADE"
    assert parsed.metadata["instrument_universe_compatibility"] == "compatible"
    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"


def test_run_pipeline_rejects_cross_asset_model_on_unrelated_symbol():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Micro contract retail lag on GOLD after CPI", use_llm=False)

    assert parsed.primary_model_id == "MICRO_CONTRACT_RETAIL_LAG"
    assert parsed.metadata["instrument_universe_compatibility"] == "unsupported_instruments"
    with pytest.raises(ValueError, match="not compatible with model MICRO_CONTRACT_RETAIL_LAG"):
        run_pipeline._resolve_target_symbol(parsed, None)


def test_run_pipeline_rejects_cross_asset_source_leg_as_target():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="manual ES to MES lead lag",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={},
        primary_model_id="ES_MES_LEAD_LAG",
        metadata={
            "instrument_universe_compatibility": "compatible",
            "compatible_instrument_universe": ["ES", "MES"],
            "target_instrument_universe": ["MES"],
        },
    )

    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"
    with pytest.raises(ValueError, match="--symbol ES is not compatible with target instruments"):
        run_pipeline._resolve_target_symbol(parsed, "ES")


def test_run_pipeline_allows_cross_asset_source_legs_but_targets_response_leg():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("ES MES lead lag after CPI", use_llm=False)

    assert parsed.primary_model_id == "ES_MES_LEAD_LAG"
    assert parsed.metadata["instrument_universe_compatibility"] == "compatible"
    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"
    with pytest.raises(ValueError, match="--symbol ES is not compatible with target instruments"):
        run_pipeline._resolve_target_symbol(parsed, "ES")


def test_run_pipeline_allows_explicit_cross_asset_target_when_only_source_leg_parsed():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Micro contract retail lag on ES after CPI", use_llm=False)

    assert parsed.primary_model_id == "MICRO_CONTRACT_RETAIL_LAG"
    assert parsed.metadata["compatible_instrument_universe"] == ["ES"]
    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"
    assert run_pipeline._resolve_target_symbol(parsed, "MES") == "MES"
    with pytest.raises(ValueError, match="--symbol ES is not compatible with target instruments"):
        run_pipeline._resolve_target_symbol(parsed, "ES")


def test_run_pipeline_rejects_explicit_missing_valid_instrument_universe():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import ParsedHypothesis

    manual = ParsedHypothesis(
        thesis="manual",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={},
        primary_model_id="MODEL_WITHOUT_VALID_UNIVERSE",
        metadata={"instrument_universe_compatibility": "missing_valid_instrument_universe"},
    )

    with pytest.raises(ValueError, match="does not declare valid_instrument_universe"):
        run_pipeline._resolve_target_symbol(manual, None)


def test_run_pipeline_resolves_compatible_empty_metadata_to_parsed_symbol():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import ParsedHypothesis

    parsed = ParsedHypothesis(
        thesis="manual compatible",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={},
        primary_model_id="AGGRESSOR_DECELERATION_FADE",
        metadata={
            "instrument_universe_compatibility": "compatible",
            "compatible_instrument_universe": [],
        },
    )

    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"


def _rl_rows() -> list[dict[str, float]]:
    return [
        {"order_book_imbalance": -0.4, "queue_imbalance": -0.2, "reward": -0.03},
        {"order_book_imbalance": 0.6, "queue_imbalance": 0.3, "reward": 0.08},
        {"order_book_imbalance": 0.2, "queue_imbalance": 0.4, "reward": 0.04},
        {"order_book_imbalance": -0.5, "queue_imbalance": -0.1, "reward": -0.02},
        {"order_book_imbalance": 0.7, "queue_imbalance": 0.5, "reward": 0.10},
    ]


def test_train_rl_agent_deterministic_and_research_blocked():
    from research_pipeline.rl_agents import train_rl_agent

    artifact_a = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=7,
        episodes=3,
        max_steps_per_episode=4,
        epsilon=0.0,
    )
    artifact_b = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=7,
        episodes=3,
        max_steps_per_episode=4,
        epsilon=0.0,
    )

    assert artifact_a == artifact_b
    assert artifact_a["status"] == "trained_research_only"
    assert artifact_a["promotion_status"] == "blocked_downstream_validation_required"
    assert artifact_a["promotable"] is False
    assert artifact_a["training_budget"]["updates_used"] > 0
    assert artifact_a["metrics"]["audit_status"] == "chronology_not_audited"
    assert artifact_a["q_table"]
    assert artifact_a["policy"]


def test_train_rl_agent_episode_start_preserves_budget_when_possible():
    from research_pipeline.rl_agents import train_rl_agent

    artifact = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=7,
        episodes=1,
        max_steps_per_episode=4,
        train_fraction=0.8,
        epsilon=0.0,
    )

    assert artifact["training_budget"]["updates_used"] == 4
    assert artifact["training_budget"]["budget_exhausted"] is True


def test_train_rl_agent_default_reward_requires_reward_key():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {"order_book_imbalance": -0.4},
        {"order_book_imbalance": 0.2},
        {"order_book_imbalance": 0.6},
    ]

    with pytest.raises(ValueError, match="must contain at least one reward key"):
        train_rl_agent(rows, ["order_book_imbalance"], seed=7, episodes=1, epsilon=0.0)


def test_train_rl_agent_custom_reward_allows_feature_only_rows():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {"order_book_imbalance": -0.4},
        {"order_book_imbalance": 0.2},
        {"order_book_imbalance": 0.6},
    ]

    artifact = train_rl_agent(
        rows,
        ["order_book_imbalance"],
        reward_function=lambda row, action, next_row, step_index: 0.0,
        seed=7,
        episodes=1,
        epsilon=0.0,
    )

    assert artifact["status"] == "trained_research_only"
    assert artifact["metrics"]["total_eval_reward"] == 0.0


def test_train_rl_agent_feature_bin_uses_zero_epsilon():
    from research_pipeline.rl_agents import _feature_bin

    assert _feature_bin(0.0) == "zero"
    assert _feature_bin(1e-10) == "zero"
    assert _feature_bin(-1e-10) == "zero"
    assert _feature_bin(1e-8) == "pos"
    assert _feature_bin(-1e-8) == "neg"


def test_train_rl_agent_audits_monotonic_timestamps():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {**row, "timestamp_ns": idx + 1}
        for idx, row in enumerate(_rl_rows())
    ]
    artifact = train_rl_agent(
        rows,
        ["order_book_imbalance", "queue_imbalance"],
        seed=7,
        episodes=2,
    )

    assert artifact["train_eval_split"]["chronology_status"] == "monotonic_timestamp"
    assert artifact["metrics"]["audit_status"] == "chronology_audited"


def test_train_rl_agent_marks_non_monotonic_timestamps_not_gateable():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {**row, "timestamp_ns": timestamp}
        for row, timestamp in zip(_rl_rows(), [1, 2, 2, 4, 5])
    ]

    artifact = train_rl_agent(rows, ["order_book_imbalance", "queue_imbalance"])

    assert artifact["status"] == "trained_research_only"
    assert artifact["train_eval_split"]["chronology_status"] == "non_monotonic_timestamp"
    assert artifact["train_eval_split"]["timestamp_field"] == "timestamp_ns"
    assert artifact["metrics"]["audit_status"] == "chronology_not_audited"


def test_train_rl_agent_rejects_invalid_timestamp_on_validated_rows():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {"timestamp_ns": "bad", "order_book_imbalance": 0.2, "reward": 0.01},
        {"timestamp_ns": 2, "order_book_imbalance": 0.3, "reward": 0.02},
    ]

    with pytest.raises(ValueError, match="row 0 timestamp_ns"):
        train_rl_agent(rows, ["order_book_imbalance"])


def test_train_rl_agent_rejects_malformed_feature_input():
    from research_pipeline.rl_agents import train_rl_agent

    rows = _rl_rows()
    del rows[1]["queue_imbalance"]

    with pytest.raises(ValueError, match="missing feature 'queue_imbalance'"):
        train_rl_agent(rows, ["order_book_imbalance", "queue_imbalance"])


@pytest.mark.parametrize("feature_name", ["order_book|imbalance", "order_book=imbalance"])
def test_train_rl_agent_rejects_state_key_delimiters(feature_name):
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {feature_name: 0.1, "reward": 0.01},
        {feature_name: -0.2, "reward": -0.01},
    ]

    with pytest.raises(ValueError, match="state-key delimiters"):
        train_rl_agent(rows, [feature_name])


def test_train_rl_agent_rejects_label_like_feature_names():
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {"future_return": 0.1, "order_book_imbalance": 0.2, "reward": 0.01},
        {"future_return": -0.2, "order_book_imbalance": -0.1, "reward": -0.01},
    ]

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        train_rl_agent(rows, ["future_return"])


@pytest.mark.parametrize(
    "feature_name",
    ["futureReturn", "nextMid", "targetLabel", "pnlNet", "PnLNet", "PNLNet"],
)
def test_train_rl_agent_rejects_camel_case_label_like_feature_names(feature_name):
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {feature_name: 0.1, "order_book_imbalance": 0.2, "reward": 0.01},
        {feature_name: -0.2, "order_book_imbalance": -0.1, "reward": -0.01},
    ]

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        train_rl_agent(rows, [feature_name])


@pytest.mark.parametrize(
    "feature_name",
    [
        "daily_pnl_net",
        "session_profit_outcome",
        "dailyPNLNet",
        "grossPnlNet",
        "net_pnl",
        "gross_pnl",
        "gross_profit",
        "realized_pnl",
        "daily_profit",
        "cumulative_return",
    ],
)
def test_train_rl_agent_rejects_prefixed_pnl_profit_label_like_feature_names(feature_name):
    from research_pipeline.rl_agents import train_rl_agent

    rows = [
        {feature_name: 0.1, "order_book_imbalance": 0.2, "reward": 0.01},
        {feature_name: -0.2, "order_book_imbalance": -0.1, "reward": -0.01},
    ]

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        train_rl_agent(rows, [feature_name])


def test_train_rl_agent_allows_pit_financial_feature_names():
    from research_pipeline.rl_agents import train_rl_agent

    feature_names = [
        "realized_vol_20d",
        "log_return_1bar",
        "close_return_zscore",
        "vol_return_ma",
        "book_profit_factor",
        "post_event_imbalance",
    ]
    rows = [
        {name: 0.1 * (idx + 1) for idx, name in enumerate(feature_names)} | {"reward": 0.01},
        {name: -0.1 * (idx + 1) for idx, name in enumerate(feature_names)} | {"reward": -0.01},
        {name: 0.05 * (idx + 1) for idx, name in enumerate(feature_names)} | {"reward": 0.02},
    ]

    artifact = train_rl_agent(rows, feature_names, episodes=1, max_steps_per_episode=2)

    assert artifact["status"] == "trained_research_only"
    assert artifact["feature_names"] == feature_names


def test_train_rl_agent_does_not_wrap_training_rows():
    from research_pipeline.rl_agents import train_rl_agent

    transitions = []

    def reward(row, action, next_row, step_index):
        transitions.append(
            (
                row["order_book_imbalance"],
                None if next_row is None else next_row["order_book_imbalance"],
            )
        )
        return 0.0

    rows = [
        {"order_book_imbalance": -2.0, "reward": 0.0},
        {"order_book_imbalance": -1.0, "reward": 0.0},
        {"order_book_imbalance": 0.0, "reward": 0.0},
        {"order_book_imbalance": 1.0, "reward": 0.0},
        {"order_book_imbalance": 2.0, "reward": 0.0},
    ]
    train_rl_agent(
        rows,
        ["order_book_imbalance"],
        reward_function=reward,
        seed=3,
        episodes=12,
        max_steps_per_episode=4,
        train_fraction=0.8,
    )

    assert (1.0, -2.0) not in transitions
    assert all(next_value is None or next_value >= value for value, next_value in transitions)


def test_train_rl_agent_records_budget_exhaustion():
    from research_pipeline.rl_agents import train_rl_agent

    artifact = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=11,
        episodes=5,
        max_steps_per_episode=4,
        max_updates=3,
    )

    assert artifact["training_budget"]["updates_used"] == 3
    assert artifact["training_budget"]["budget_exhausted"] is True

    natural = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=11,
        episodes=1,
        max_steps_per_episode=1,
    )
    assert natural["training_budget"]["updates_used"] == 1
    assert natural["training_budget"]["budget_exhausted"] is True


def test_validate_rl_artifact_rejects_promotable_policy():
    from research_pipeline.rl_agents import train_rl_agent, validate_rl_artifact

    artifact = train_rl_agent(
        _rl_rows(),
        ["order_book_imbalance", "queue_imbalance"],
        seed=7,
        episodes=2,
        max_steps_per_episode=3,
    )
    artifact["promotable"] = True

    with pytest.raises(ValueError, match="non-promotable"):
        validate_rl_artifact(artifact)


def test_run_pipeline_dry_run_rl_writes_policy_artifact(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    rows_path = tmp_path / "rl_rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(row) for row in _rl_rows()) + "\n",
        encoding="utf-8",
    )

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_rl")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        },
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
        "--no-llm",
        "--max-candidates",
        "2",
        "--rl",
        "--rl-training-data",
        str(rows_path),
        "--rl-feature",
        "order_book_imbalance",
        "--rl-feature",
        "queue_imbalance",
        "--rl-seed",
        "7",
    ])

    assert run_pipeline.main() == 0
    payload = json.loads(capsys.readouterr().out)
    artifact = payload["rl_policy_artifact"]
    artifact_path = (
        tmp_path
        / "research_cards"
        / "pipeline_runs"
        / "pipeline_test_rl"
        / "rl_policy_artifact.json"
    )

    assert artifact["status"] == "trained_research_only"
    assert artifact["promotion_status"] == "blocked_downstream_validation_required"
    assert artifact["promotable"] is False
    assert artifact_path.is_file()


def test_rl_research_only_does_not_match_blocked_artifact():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.rl_agents import blocked_rl_artifact

    blocked = blocked_rl_artifact(reason="missing_training_data")

    assert run_pipeline._rl_artifact_blocked(blocked) is True
    assert run_pipeline._rl_research_only(blocked) is False
    assert run_pipeline._rl_research_only({"status": "trained_research_only"}) is True


def test_run_pipeline_rl_blocked_stops_before_vectorbt(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_rl_blocked")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "filter_candidates",
        lambda *args, **kwargs: pytest.fail("blocked RL should stop before VectorBT"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--vectorbt-only",
        "--rl",
    ])

    assert run_pipeline.main() == 2
    payload = _last_json_object(capsys.readouterr().out)

    assert payload["status"] == "blocked_rl_research_process"
    assert payload["rl_policy_artifact"]["status"] == "blocked"
    assert payload["rl_policy_artifact"]["failure_reasons"] == ["missing_training_data"]


def test_run_pipeline_trained_rl_stops_before_deploy(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    rows_path = tmp_path / "rl_rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(row) for row in _rl_rows()) + "\n",
        encoding="utf-8",
    )

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_rl_research_only")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "evaluate_candidate_events",
        lambda *args, **kwargs: pytest.fail("research-only RL should stop before evaluation"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--rl",
        "--rl-training-data",
        str(rows_path),
        "--rl-feature",
        "order_book_imbalance",
        "--rl-feature",
        "queue_imbalance",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)

    assert payload["status"] == "rl_research_artifact_written"
    assert payload["rl_policy_artifact"]["status"] == "trained_research_only"
    assert payload["rl_policy_artifact"]["promotable"] is False


def test_run_pipeline_trained_rl_stops_before_vectorbt(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    rows_path = tmp_path / "rl_rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(row) for row in _rl_rows()) + "\n",
        encoding="utf-8",
    )

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_rl_vectorbt_stop")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "filter_candidates",
        lambda *args, **kwargs: pytest.fail("research-only RL should stop before VectorBT"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--vectorbt-only",
        "--rl",
        "--rl-training-data",
        str(rows_path),
        "--rl-feature",
        "order_book_imbalance",
        "--rl-feature",
        "queue_imbalance",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)

    assert payload["status"] == "rl_research_artifact_written"
    assert payload["rl_policy_artifact"]["status"] == "trained_research_only"


def test_run_pipeline_dry_run_trained_rl_skips_vectorbt_flag(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    rows_path = tmp_path / "rl_rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(row) for row in _rl_rows()) + "\n",
        encoding="utf-8",
    )

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_test_rl_dry_vectorbt_skip")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        },
    )
    monkeypatch.setattr(
        run_pipeline,
        "filter_candidates",
        lambda *args, **kwargs: pytest.fail("RL dry-run artifact inspection should skip VectorBT"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--repo-root",
        str(tmp_path),
        "--dry-run",
        "--no-llm",
        "--vectorbt-only",
        "--rl",
        "--rl-training-data",
        str(rows_path),
        "--rl-feature",
        "order_book_imbalance",
        "--rl-feature",
        "queue_imbalance",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)

    assert "vectorbt_filter" not in payload
    assert payload["rl_policy_artifact"]["status"] == "trained_research_only"


def test_run_pipeline_autoresearch_rl_rejected(monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_request",
        lambda **kwargs: pytest.fail("--autoresearch --rl should fail before artifact writes"),
    )
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_2024_09_11_TIGHT",
        "--autoresearch",
        "--rl",
    ])

    assert run_pipeline.main() == 2
    assert "--rl is implemented for single pipeline runs" in capsys.readouterr().err


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


def test_candidates_from_ideas_skips_symbol_resolution_errors():
    from research_pipeline.idea_generation import candidates_from_ideas

    packet = _idea_packet()
    packet["ideas"] = [
        json.loads(json.dumps(packet["ideas"][0])),
        json.loads(json.dumps(packet["ideas"][1])),
    ]

    def resolver(parsed):
        if parsed.primary_model_id == "BOOK_PRESSURE":
            raise ValueError("bad symbol")
        return "MES"

    candidates = candidates_from_ideas(
        packet,
        max_candidates=2,
        target_symbol_resolver=resolver,
    )
    by_id = {idea["idea_id"]: idea for idea in packet["ideas"]}

    assert by_id["idea_low"]["status"] == "static_reject"
    assert "target_symbol_resolution_failed" in by_id["idea_low"]["static_error_codes"]
    assert candidates
    assert {candidate.metadata["idea_id"] for candidate in candidates} == {"idea_high"}


def test_candidates_from_ideas_fails_when_all_symbol_resolution_fails():
    from research_pipeline.idea_generation import candidates_from_ideas

    packet = _idea_packet()
    packet["ideas"] = [json.loads(json.dumps(packet["ideas"][1]))]

    def resolver(parsed):
        raise ValueError(f"bad symbol for {parsed.primary_model_id}")

    with pytest.raises(ValueError, match="bad symbol for SPREAD_BLOWOUT_RECOMPRESSION"):
        candidates_from_ideas(packet, max_candidates=2, target_symbol_resolver=resolver)

    assert packet["ideas"][0]["status"] == "static_reject"
    assert "target_symbol_resolution_failed" in packet["ideas"][0]["static_error_codes"]


def test_candidates_from_ideas_skips_candidate_generation_value_errors():
    from research_pipeline.idea_generation import candidates_from_ideas

    packet = _idea_packet()
    packet["ideas"] = [
        json.loads(json.dumps(packet["ideas"][0])),
        json.loads(json.dumps(packet["ideas"][1])),
    ]
    packet["ideas"][0]["param_ranges"] = {
        "signal_threshold": [0.05, 0.35],
        "stop_loss": [0.01, 0.03],
        "stop_loss_pct": [0.01, 0.03],
    }

    candidates = candidates_from_ideas(packet, max_candidates=2)
    by_id = {idea["idea_id"]: idea for idea in packet["ideas"]}

    assert by_id["idea_low"]["status"] == "static_reject"
    assert "candidate_generation_failed" in by_id["idea_low"]["static_error_codes"]
    assert candidates
    assert {candidate.metadata["idea_id"] for candidate in candidates} == {"idea_high"}


def test_parsed_from_idea_canonicalizes_instrument_aliases():
    from research_pipeline.idea_generation import parsed_from_idea

    packet = _idea_packet()
    idea = packet["ideas"][1]
    idea["instrument_ids"] = ["MICRO ES"]

    parsed = parsed_from_idea(idea)

    assert parsed.instrument_universe == ["MES"]
    assert parsed.metadata["instrument_universe_compatibility"] == "compatible"


def test_idea_set_cli_threads_search_controls_to_generated_candidates(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    packet = _idea_packet()
    packet["ideas"] = [packet["ideas"][0]]
    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_idea_search_controls")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: packet)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Trade book pressure after CPI",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
        "--idea-set",
        "--max-candidates",
        "2",
        "--search-method",
        "bayesian",
        "--search-seed",
        "17",
        "--no-hybrid",
    ])

    assert run_pipeline.main() == 0
    payload = _last_json_object(capsys.readouterr().out)
    assert [candidate["target_symbol"] for candidate in payload["candidates"]] == ["MES", "MES"]
    search_meta = [
        candidate["metadata"]["parameter_search"]
        for candidate in payload["candidates"]
    ]
    assert {meta["search_method"] for meta in search_meta} == {"bayesian"}
    assert {meta["method_status"] for meta in search_meta} == {"method_unavailable"}
    assert {meta["fallback_method"] for meta in search_meta} == {"seeded"}
    assert {meta["seed"] for meta in search_meta} == {17}


def test_idea_set_cli_rejects_mixed_symbol_candidates_before_dedup(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    packet = _idea_packet()
    base = packet["ideas"][0]
    idea_mes = json.loads(json.dumps(base))
    idea_mnq = json.loads(json.dumps(base))
    idea_mes["idea_id"] = "idea_mes"
    idea_mes["instrument_ids"] = ["MES"]
    idea_mnq["idea_id"] = "idea_mnq"
    idea_mnq["instrument_ids"] = ["MNQ"]
    packet["ideas"] = [idea_mes, idea_mnq]
    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_idea_mixed_symbols")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: packet)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Trade book pressure after CPI",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
        "--idea-set",
        "--max-candidates",
        "2",
        "--no-hybrid",
    ])

    assert run_pipeline.main() == 2
    assert "--idea-set produced multiple target symbols" in capsys.readouterr().err


def test_idea_set_cli_rejects_unsupported_instrument(tmp_path, monkeypatch, capsys):
    import sys

    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "event_ids": kwargs.get("event_ids"),
            "openfoundry_meta": {
                "connector_id": "test",
                "asset_class": "test",
                "vendor_shas": {},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    packet = _idea_packet()
    packet["ideas"] = [packet["ideas"][1]]
    packet["ideas"][0]["instrument_ids"] = ["GC"]
    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_idea_bad_symbol")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: packet)
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py",
        "--thesis",
        "Fade spread blowout after CPI",
        "--event-id",
        "CPI_1",
        "--repo-root",
        str(tmp_path),
        "--no-llm",
        "--dry-run",
        "--idea-set",
    ])

    assert run_pipeline.main() == 2
    assert "not compatible with model SPREAD_BLOWOUT_RECOMPRESSION" in capsys.readouterr().err


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


def test_idea_set_full_run_requires_prefilter():
    import scripts.run_pipeline as run_pipeline

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


def test_deploy_best_does_not_fallback_to_failing_result(tmp_path):
    from research_pipeline.deployment import deploy_best
    from research_pipeline.types import (
        CandidateModel,
        EvaluationResult,
        GateThresholds,
        ParsedHypothesis,
        PipelineReport,
    )

    parsed = ParsedHypothesis(
        thesis="x",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=[],
        param_ranges={},
        primary_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    )
    candidate = CandidateModel(
        candidate_id="c_fail",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={},
        thesis="x",
    )
    result = EvaluationResult(
        candidate=candidate,
        event_id="CPI_1",
        net_pnl=10.0,
        num_trades=0,
        win_rate=0.0,
        expectancy=0.0,
        tail_loss=0.0,
        gates=GateThresholds(min_trades=1),
    )
    report = PipelineReport(
        run_id="pipeline_no_deploy",
        thesis="x",
        event_id="CPI_1",
        parsed=parsed,
        candidates_tested=1,
        results=[result],
        selected=None,
        artifact_dir=None,
    )

    assert result.passes_all_gates() is False
    assert deploy_best(tmp_path, report) is None
    assert report.selected is None


def test_deployment_packet_is_non_promotable_without_downstream_authority(tmp_path):
    from research_pipeline.deployment import _build_packet
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel(
        candidate_id="c_pass",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={},
        thesis="x",
    )
    result = EvaluationResult(
        candidate=candidate,
        event_id="CPI_1",
        net_pnl=10.0,
        num_trades=1,
        win_rate=1.0,
        expectancy=10.0,
        tail_loss=0.0,
        gates=GateThresholds(min_trades=1),
    )

    assert result.passes_all_gates() is True
    packet = _build_packet("run", "CPI_1", candidate, result, tmp_path)
    assert packet["latency_authority"]["promote_candidate"] is False
    assert packet["latency_authority"]["promotion_blocked_reason"] == (
        "research_pipeline_requires_downstream_screening_realism"
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
        "evaluate_candidate_events",
        lambda *args, **kwargs: pytest.fail("vectorbt-only called HftBacktest evaluate"),
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
        "evaluate_candidate_events",
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
        "evaluate_candidate_events",
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


def test_hypothesis_packet_accepts_enriched_fields(monkeypatch):
    from data_layer.llm import openai_compatible_client as llm_client
    from data_layer.llm.packet_runner import run_llm_on_hypothesis_request
    from research_pipeline.packets import build_pipeline_request

    request = build_pipeline_request(
        request_id="req_hyp_enriched",
        thesis="Fade GOLD blowout after CPI",
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=REPO,
        max_candidates=3,
    )
    mock_body = json.dumps(
        {
            "schema_version": "1",
            "request_id": "req_hyp_enriched",
            "llm_model": "mock-gpt55",
            "llm_status": "ok",
            "primary_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
            "instrument_universe": ["GC"],
            "entry_rules": [],
            "exit_rules": [],
            "indicators": ["spread"],
            "feature_list": ["SPREAD_BLOWOUT_RECOMPRESSION"],
            "param_ranges": {"signal_threshold": [0.02, 0.15]},
            "entry_rule": "fade spread after blowout",
            "exit_rule": "exit on recompression",
            "target_instruments": ["GOLD"],
            "indicative_stop_loss": 0.12,
            "expected_holding_period": 5,
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
        "Fade GOLD blowout after CPI",
        allowed_model_ids=["SPREAD_BLOWOUT_RECOMPRESSION"],
        repo_root=REPO,
    )

    assert out["llm_status"] == "ok"
    assert out["entry_rule"] == "fade spread after blowout"
    assert out["target_instruments"] == ["GOLD"]


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
    assert parsed.source == "hypothesis_packet"


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
