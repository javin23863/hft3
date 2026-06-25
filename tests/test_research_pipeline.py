"""Tests for packages/research_pipeline."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_parse_hypothesis_uses_model_alias_and_registry_ranges():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Run a blowout fade on GOLD after CPI", use_llm=False)

    assert parsed.primary_model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert parsed.param_ranges["signal_threshold"] == [0.02, 0.15]
    assert parsed.param_ranges["stop_loss"] == [0.05, 0.30]
    assert parsed.metadata["volatility_regime"] == "high_volatility"
    assert parsed.metadata["instrument_universe_compatibility"] == "unsupported_instruments"
    assert parsed.metadata["unsupported_instruments"] == ["GC"]


def test_parse_hypothesis_keeps_mixed_unsupported_context_blocked():
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(
        "Run a blowout fade on ES while watching GOLD after CPI",
        use_llm=False,
    )

    assert "ES" in parsed.instrument_universe
    assert "GC" in parsed.instrument_universe
    assert parsed.metadata["compatible_instrument_universe"] == ["ES"]
    assert parsed.metadata["instrument_universe_compatibility"] == "unsupported_instruments"
    assert parsed.metadata["unsupported_instruments"] == ["GC"]


def test_parse_hypothesis_structural_alias_does_not_route_as_primary_model():
    from features_engine.src.model_registry import load_model_registry
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Use book pressure after CPI", use_llm=False)
    model_entry = load_model_registry()["models"][parsed.primary_model_id]

    assert parsed.primary_model_id != "BOOK_PRESSURE"
    assert model_entry["kind"] == "hypothesis"


def test_parse_hypothesis_parenthesized_structural_slug_does_not_route_primary():
    from features_engine.src.model_registry import load_model_registry
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(
        "Use queue imbalance (BOOK_PRESSURE) after CPI",
        use_llm=False,
    )
    model_entry = load_model_registry()["models"][parsed.primary_model_id]

    assert parsed.primary_model_id != "BOOK_PRESSURE"
    assert model_entry["kind"] == "hypothesis"


def test_parse_hypothesis_packet_structural_primary_falls_back_to_hypothesis():
    from features_engine.src.model_registry import load_model_registry
    from research_pipeline.hypothesis_parser import _from_llm_dict

    parsed = _from_llm_dict(
        "Use book pressure after CPI",
        {
            "instrument_universe": ["MES"],
            "entry_rules": ["enter_pressure"],
            "exit_rules": ["exit_revert"],
            "indicators": ["BOOK_PRESSURE"],
            "feature_list": ["BOOK_PRESSURE"],
            "param_ranges": {"signal_threshold": [0.05, 0.35]},
            "primary_model_id": "BOOK_PRESSURE",
        },
    )
    model_entry = load_model_registry()["models"][parsed.primary_model_id]

    assert parsed.primary_model_id != "BOOK_PRESSURE"
    assert model_entry["kind"] == "hypothesis"


def test_model_registry_declares_valid_instrument_universe_for_all_models():
    from features_engine.src.model_registry import load_model_registry

    missing = [
        slug
        for slug, entry in load_model_registry()["models"].items()
        if not entry.get("valid_instrument_universe")
    ]

    assert missing == []


def test_run_pipeline_resolves_cross_asset_target_symbol():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("ES MES lead lag after CPI", use_llm=False)

    assert parsed.primary_model_id == "ES_MES_LEAD_LAG"
    assert run_pipeline._resolve_target_symbol(parsed, None) == "MES"
    with pytest.raises(ValueError, match="--symbol ES is not compatible with target instruments"):
        run_pipeline._resolve_target_symbol(parsed, "ES")


def test_run_pipeline_accepts_canonical_symbol_variant():
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis("Fade spread blowout after CPI release on MES", use_llm=False)

    assert run_pipeline._resolve_target_symbol(parsed, "MES.v.0") == "MES.v.0"


def test_run_pipeline_derives_target_symbol_from_parsed_instrument(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "hft3-cme-mbo",
                "asset_class": "cme_mbo_microstructure",
                "vendor_shas": {"openfoundry": "test"},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_symbol_derived")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)

    code = run_pipeline.main(
        [
            "--thesis",
            "Trade micro NQ futures after CPI",
            "--event-id",
            "CPI_1",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 0
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["parsed"]["target_symbol"] == "MNQ"
    assert payload["candidates"][0]["target_symbol"] == "MNQ"


def test_run_pipeline_rejects_cli_symbol_mismatch(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "hft3-cme-mbo",
                "asset_class": "cme_mbo_microstructure",
                "vendor_shas": {"openfoundry": "test"},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_symbol_mismatch")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)

    code = run_pipeline.main(
        [
            "--thesis",
            "Trade micro NQ futures after CPI",
            "--event-id",
            "CPI_1",
            "--symbol",
            "MES",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--no-llm",
        ]
    )

    assert code == 2
    assert "--symbol MES is not compatible" in capsys.readouterr().err


def test_run_pipeline_idea_set_parses_after_static_filter(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline

    idea_packet = _idea_packet()
    idea_packet["constraints"]["allowed_model_ids"] = ["ES_MES_LEAD_LAG"]
    idea_packet["ideas"] = [
        {
            "idea_id": "idea_cross_asset",
            "status": "proposed",
            "lane_code": "cme",
            "thesis_code": "es_mes_lead_lag",
            "instrument_ids": ["ES", "MES"],
            "primary_model_id": "ES_MES_LEAD_LAG",
            "feature_ids": ["ES_MES_LEAD_LAG"],
            "param_ranges": {"signal_threshold": [0.05, 0.35]},
            "entry_rule_codes": ["enter_lead_lag"],
            "exit_rule_codes": ["exit_revert"],
            "risk_codes": ["latency_gate_required"],
            "evidence_ref_ids": ["mem_001"],
            "rank_inputs": {
                "novelty": 0.8,
                "evidence_coverage": 0.8,
                "lane_fit": 1.0,
                "prior_failure_overlap": 0.0,
                "validation_readiness": 1.0,
            },
        }
    ]

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {
                "connector_id": "hft3-cme-mbo",
                "asset_class": "cme_mbo_microstructure",
                "vendor_shas": {"openfoundry": "test"},
                "schema_version": "1",
            },
            "max_candidates": kwargs["max_candidates"],
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_idea_after_filter")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "generate_idea_set", lambda *args, **kwargs: idea_packet)

    code = run_pipeline.main(
        [
            "--thesis",
            "fallback spread thesis on MES",
            "--event-id",
            "CPI_1",
            "--repo-root",
            str(tmp_path),
            "--idea-set",
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 0
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["parsed"]["primary_model_id"] == "ES_MES_LEAD_LAG"
    assert payload["parsed"]["target_symbol"] == "MES"
    assert payload["candidates"][0]["model_id"] == "ES_MES_LEAD_LAG"
    assert payload["candidates"][0]["target_symbol"] == "MES"


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
                "candidate_search": {"method": "bayesian", "seed": 13},
                "rl_training": {
                    "features": ["order_book_imbalance"],
                    "device": "cpu",
                    "seed": 99,
                    "cache": {
                        "enabled": False,
                        "root": "runtime/test_rl_cache",
                    },
                },
                "evaluation": {"workers": 3},
                "gate_profiles": {
                    "default_profile": "high_volatility",
                    "profiles": {
                        "high_volatility": {
                            "min_net_pnl": 0.0,
                            "min_trades": 10,
                            "max_tail_loss": 5000.0,
                            "min_win_rate": 0.45,
                        }
                    },
                },
                "edge_evaluation": {
                    "statistics": {"min_psr": 0.95, "min_dsr": 0.9},
                    "validation": {"cscv": True, "cscv_subsets": 4, "max_pbo": 0.2},
                    "cost_model": {"commission_per_trade": 1.25, "slippage_bps": 0.5},
                    "risk": {"min_tail_ratio": 1.1, "max_cvar_95": 25.0},
                },
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
        candidate_search_method=None,
        candidate_search_seed=None,
        evaluation_workers=None,
        gate_profile=None,
        gate_min_net_pnl=None,
        gate_min_trades=None,
        gate_max_tail_loss=None,
        gate_min_win_rate=None,
    )

    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)

    assert args.max_candidates == 7
    assert args.vectorbt_scope == "paid-compute"
    assert args.vectorbt_max_trials == 3
    assert args.vectorbt_max_total_trials == 21
    assert args.max_ideas == 4
    assert args.review_memory_limit == 2
    assert args.candidate_search_method == "bayesian"
    assert args.candidate_search_seed == 13
    assert args.rl_feature == ["order_book_imbalance"]
    assert args.rl_seed == 99
    assert args.rl_cache_enabled is False
    assert args.rl_cache_root == "runtime/test_rl_cache"
    assert args.evaluation_workers == 3
    assert args.gate_profile == "high_volatility"
    assert args.gate_min_trades == 10
    assert args.gate_min_win_rate == 0.45
    assert args.min_psr == 0.95
    assert args.min_dsr == 0.9
    assert args.cscv is True
    assert args.cscv_subsets == 4
    assert args.max_pbo == 0.2
    assert args.commission_per_trade == 1.25
    assert args.slippage_bps == 0.5
    assert args.min_tail_ratio == 1.1
    assert args.max_cvar_95 == 25.0
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


def test_pipeline_runtime_config_rejects_invalid_edge_probabilities(tmp_path):
    import argparse

    import pytest
    import scripts.run_pipeline as run_pipeline

    cfg_path = tmp_path / "runtime.json"
    cfg_path.write_text(
        json.dumps({"edge_evaluation": {"validation": {"max_pbo": 1.2}}}),
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

    with pytest.raises(ValueError, match="max_pbo must be between 0 and 1"):
        run_pipeline._apply_pipeline_runtime_defaults(args, cfg)


@pytest.mark.parametrize(
    ("attr", "message"),
    [
        ("min_tail_ratio", "min_tail_ratio must be nonnegative"),
        ("max_cvar_95", "max_cvar_95 must be nonnegative"),
        ("max_cvar_99", "max_cvar_99 must be nonnegative"),
    ],
)
def test_pipeline_runtime_config_rejects_invalid_cli_edge_risk_thresholds(attr, message):
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg = run_pipeline.load_pipeline_runtime_config()
    values = {
        "min_tail_ratio": None,
        "max_cvar_95": None,
        "max_cvar_99": None,
    }
    values[attr] = -1.0
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
        **values,
    )

    with pytest.raises(ValueError, match=message):
        run_pipeline._apply_pipeline_runtime_defaults(args, cfg)


def test_model_registry_volatility_regime_selects_gate_profile():
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg = run_pipeline.load_pipeline_runtime_config()
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
        candidate_search_method=None,
        candidate_search_seed=None,
        rl_training_data=None,
        rl_required=False,
        rl_device=None,
        rl_seed=None,
        rl_feature=None,
        evaluation_workers=None,
        gate_profile=None,
        gate_min_net_pnl=None,
        gate_min_trades=None,
        gate_max_tail_loss=None,
        gate_min_win_rate=None,
    )
    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)

    resolution = run_pipeline._apply_registry_gate_profile(
        args,
        cfg,
        "SPREAD_BLOWOUT_RECOMPRESSION",
    )

    assert {
        key: resolution[key]
        for key in ("source", "model_id", "volatility_regime", "profile")
    } == {
        "source": "model_registry_volatility_regime",
        "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
        "volatility_regime": "high_volatility",
        "profile": "high_volatility",
    }
    assert resolution["thresholds"] == {
        "min_net_pnl": 0.0,
        "min_trades": 10,
        "max_tail_loss": 5000.0,
        "min_win_rate": 0.45,
        "min_sharpe": -1000000000.0,
        "min_sortino": -1000000000.0,
        "max_drawdown": 1000000000.0,
    }
    assert resolution["threshold_cli_overrides"] == {
        "min_net_pnl": False,
        "min_trades": False,
        "max_tail_loss": False,
        "min_win_rate": False,
        "min_sharpe": False,
        "min_sortino": False,
        "max_drawdown": False,
    }
    assert args.gate_profile == "high_volatility"
    assert args.gate_min_trades == 10
    assert args.gate_min_win_rate == 0.45


def test_cli_gate_profile_overrides_model_registry_regime():
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg = run_pipeline.load_pipeline_runtime_config()
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
        candidate_search_method=None,
        candidate_search_seed=None,
        rl_training_data=None,
        rl_required=False,
        rl_device=None,
        rl_seed=None,
        rl_feature=None,
        evaluation_workers=None,
        gate_profile="normal",
        gate_min_net_pnl=None,
        gate_min_trades=None,
        gate_max_tail_loss=None,
        gate_min_win_rate=None,
    )
    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)

    resolution = run_pipeline._apply_registry_gate_profile(
        args,
        cfg,
        "SPREAD_BLOWOUT_RECOMPRESSION",
    )

    assert resolution["source"] == "cli"
    assert resolution["profile"] == "normal"
    assert args.gate_profile == "normal"
    assert args.gate_min_trades == 0


def test_candidate_gate_plan_supports_mixed_registry_profiles():
    import argparse

    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel

    cfg = run_pipeline.load_pipeline_runtime_config()
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
        candidate_search_method=None,
        candidate_search_seed=None,
        rl_training_data=None,
        rl_required=False,
        rl_device=None,
        rl_seed=None,
        rl_feature=None,
        evaluation_workers=None,
        gate_profile=None,
        gate_min_net_pnl=None,
        gate_min_trades=None,
        gate_max_tail_loss=None,
        gate_min_win_rate=None,
    )
    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)
    candidates = [
        CandidateModel(
            candidate_id="spread",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={"signal_threshold": 0.1},
            thesis="spread",
        ),
        CandidateModel(
            candidate_id="book",
            model_id="BOOK_PRESSURE",
            strategy_params={"signal_threshold": 0.1},
            thesis="book",
        ),
    ]

    plan = run_pipeline._candidate_gate_profile_plan(args, cfg, candidates)

    assert plan["by_candidate"]["spread"]["profile"] == "high_volatility"
    assert plan["by_candidate"]["spread"]["thresholds"]["min_trades"] == 10
    assert plan["by_candidate"]["book"]["profile"] == "normal"
    assert plan["by_candidate"]["book"]["thresholds"]["min_trades"] == 0


def test_pipeline_config_receipt_includes_candidate_gate_plan(tmp_path):
    import argparse

    import scripts.run_pipeline as run_pipeline

    cfg = run_pipeline.load_pipeline_runtime_config()
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
        idea_temperature=None,
        idea_top_p=None,
        candidate_search_method=None,
        candidate_search_seed=None,
        rl_training_data=None,
        rl_required=False,
        rl_device=None,
        rl_seed=None,
        rl_feature=None,
        evaluation_workers=None,
        gate_profile=None,
        gate_min_net_pnl=None,
        gate_min_trades=None,
        gate_max_tail_loss=None,
        gate_min_win_rate=None,
    )
    run_pipeline._apply_pipeline_runtime_defaults(args, cfg)
    run_pipeline._resolve_idea_sampling_values(args, cfg)
    gate_plan = {
        "schema_version": "hft3_gate_profile_plan_v1",
        "by_candidate": {
            "spread": {
                "candidate_id": "spread",
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "source": "model_registry_volatility_regime",
                "volatility_regime": "high_volatility",
                "profile": "high_volatility",
                "thresholds": {
                    "min_net_pnl": 0.0,
                    "min_trades": 10,
                    "max_tail_loss": 5000.0,
                    "min_win_rate": 0.45,
                    "min_sharpe": -1000000000.0,
                    "min_sortino": -1000000000.0,
                    "max_drawdown": 1000000000.0,
                },
                "threshold_cli_overrides": {
                    "min_net_pnl": False,
                    "min_trades": False,
                    "max_tail_loss": False,
                    "min_win_rate": False,
                    "min_sharpe": False,
                    "min_sortino": False,
                    "max_drawdown": False,
                },
            }
        },
    }

    receipt = run_pipeline._pipeline_config_receipt(
        config=cfg,
        config_path=tmp_path / "runtime.json",
        args=args,
        gate_profile_plan=gate_plan,
    )

    assert receipt["effective"]["gate_profiles"]["candidate_gate_plan"] == gate_plan


def test_evaluation_workers_are_capped_on_msi(monkeypatch):
    from research_pipeline import runtime_policy

    monkeypatch.setattr(runtime_policy.platform, "node", lambda: "MSI")

    effective, policy = runtime_policy.effective_evaluation_workers(
        128,
        {"evaluation": {"max_workers": 64, "msi_max_workers": 1}},
    )

    assert effective == 1
    assert policy["requested_workers"] == 128
    assert policy["cap_reason"] == "msi_local_cap"


def test_evaluation_workers_are_capped_on_remote(monkeypatch):
    from research_pipeline import runtime_policy

    monkeypatch.setattr(runtime_policy.platform, "node", lambda: "chi404")

    effective, policy = runtime_policy.effective_evaluation_workers(
        128,
        {"evaluation": {"max_workers": 64, "msi_max_workers": 1}},
    )

    assert effective == 64
    assert policy["cap_reason"] == "max_workers_cap"


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
    assert receipt_a["effective"]["rl_training"]["cache"] == {
        "enabled": True,
        "root": "runtime/research_pipeline/rl_policy_cache",
    }
    assert receipt_a["pipeline_runtime_config_hash"] != receipt_b["pipeline_runtime_config_hash"]


def test_rl_training_stage_cache_miss_then_hit(tmp_path):
    import argparse

    import scripts.run_pipeline as run_pipeline

    training_path = tmp_path / "rl_rows.jsonl"
    rows = [
        {"timestamp_ns": 1, "order_book_imbalance": 0.5, "spread": 1.0, "reward": 0.10},
        {"timestamp_ns": 2, "order_book_imbalance": -0.5, "spread": 1.0, "reward": -0.20},
        {"timestamp_ns": 3, "order_book_imbalance": 0.0, "spread": 2.0, "reward": 0.00},
    ]
    training_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    artifact_dir = tmp_path / "research_cards" / "pipeline_runs" / "rl_cache_test"
    args = argparse.Namespace(
        rl_training_enabled=True,
        rl_feature=["order_book_imbalance", "spread"],
        rl_training_data=training_path,
        rl_device="cpu",
        rl_seed=9,
        rl_cache_enabled=True,
        rl_cache_root="runtime/test_rl_policy_cache",
    )

    first, first_path = run_pipeline._run_rl_training_stage(
        args,
        artifact_dir=artifact_dir,
        repo_root=tmp_path,
    )
    second, second_path = run_pipeline._run_rl_training_stage(
        args,
        artifact_dir=artifact_dir,
        repo_root=tmp_path,
    )

    assert first_path == artifact_dir / "rl_policy_artifact.json"
    assert second_path == first_path
    assert first["cache_receipt"]["status"] == "miss"
    assert second["cache_receipt"]["status"] == "hit"
    assert first["cache_receipt"]["cache_key"] == second["cache_receipt"]["cache_key"]
    assert first["promotable"] is False
    assert second["promotable"] is False
    cache_path = tmp_path / "runtime" / "test_rl_policy_cache" / f"{first['cache_receipt']['cache_key']}.json"
    assert first["cache_receipt"]["cache_path"] == str(cache_path)
    assert second["cache_receipt"]["cache_path"] == str(cache_path)
    assert first["training_summary"]["train_eval_split"]["train_rows"] == 2
    assert first["training_summary"]["train_eval_split"]["eval_rows"] == 1
    assert first["training_summary"]["training_budget"]["updates_used"] == 2
    assert cache_path.is_file()


def test_rl_training_rejects_label_like_feature_names():
    from research_pipeline.rl_agents import validate_rl_features

    with pytest.raises(ValueError, match="non-PIT or label-like"):
        validate_rl_features(["reward"])


def test_rl_training_rejects_non_monotonic_timestamps(tmp_path):
    from research_pipeline.rl_agents import train_rl_policy_artifact

    training_path = tmp_path / "rl_rows.json"
    training_path.write_text(
        json.dumps(
            [
                {"timestamp_ns": 2, "order_book_imbalance": 0.5, "reward": 0.10},
                {"timestamp_ns": 1, "order_book_imbalance": -0.5, "reward": -0.20},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-decreasing"):
        train_rl_policy_artifact(
            training_data_path=training_path,
            feature_names=["order_book_imbalance"],
            device="cpu",
        )


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


def test_evaluate_candidates_batch_uses_bounded_executor(monkeypatch, tmp_path):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    captured = {"max_workers": None, "jobs": []}

    class InlineExecutor:
        def __init__(self, max_workers):
            captured["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def map(self, func, jobs):
            captured["jobs"] = list(jobs)
            return [func(job) for job in captured["jobs"]]

    def fake_evaluate(candidate, event_id, repo_root, **kwargs):
        gates = kwargs["gates"]
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=1.0,
            num_trades=gates.min_trades,
            win_rate=1.0,
            expectancy=1.0,
            tail_loss=0.0,
            gates=gates,
        )

    monkeypatch.setattr(run_pipeline, "ProcessPoolExecutor", InlineExecutor)
    monkeypatch.setattr(run_pipeline, "evaluate_model", fake_evaluate)
    candidates = [
        CandidateModel("c1", "BOOK_PRESSURE", {}, "book"),
        CandidateModel("c2", "SPREAD_BLOWOUT_RECOMPRESSION", {}, "spread"),
    ]

    results = run_pipeline._evaluate_candidates_batch(
        candidates,
        event_id="CPI_2024_09_11_TIGHT",
        repo_root=tmp_path,
        chi404_summary=None,
        gates_by_candidate_id={
            "c1": GateThresholds(min_trades=1),
            "c2": GateThresholds(min_trades=2),
        },
        workers=2,
    )

    assert captured["max_workers"] == 2
    assert [job[0].candidate_id for job in captured["jobs"]] == ["c1", "c2"]
    assert [result.gates.min_trades for result in results] == [1, 2]


def test_evaluate_candidates_batch_aggregates_multiple_events(monkeypatch, tmp_path):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel("c_multi", "BOOK_PRESSURE", {}, "book")
    captured = {}

    def fake_evaluate_events(cand, event_ids, repo_root, **kwargs):
        captured["event_ids"] = list(event_ids)
        return EvaluationResult(
            candidate=cand,
            event_id=",".join(event_ids),
            net_pnl=1.0,
            num_trades=2,
            win_rate=1.0,
            expectancy=0.5,
            tail_loss=0.0,
            gates=kwargs["gates"],
            risk_metrics_gateable=True,
        )

    monkeypatch.setattr(run_pipeline, "evaluate_candidate_events", fake_evaluate_events)

    results = run_pipeline._evaluate_candidates_batch(
        [candidate],
        event_ids=["CPI_2024_09_11_TIGHT", "FOMC_2024_09_18_TIGHT"],
        repo_root=tmp_path,
        chi404_summary=None,
        gates_by_candidate_id={"c_multi": GateThresholds(min_trades=1)},
        workers=1,
    )

    assert captured["event_ids"] == ["CPI_2024_09_11_TIGHT", "FOMC_2024_09_18_TIGHT"]
    assert results[0].event_id == "CPI_2024_09_11_TIGHT,FOMC_2024_09_18_TIGHT"


def test_aggregate_evaluation_sortino_uses_downside_semideviation():
    from research_pipeline.evaluation import aggregate_evaluation_results
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds

    candidate = CandidateModel("c_sortino", "BOOK_PRESSURE", {}, "book")
    gates = GateThresholds(min_trades=1)

    def event_result(event_id: str, net_pnl: float) -> EvaluationResult:
        return EvaluationResult(
            candidate=candidate,
            event_id=event_id,
            net_pnl=net_pnl,
            num_trades=1,
            win_rate=1.0 if net_pnl > 0 else 0.0,
            expectancy=net_pnl,
            tail_loss=min(net_pnl, 0.0),
            gates=gates,
        )

    aggregate = aggregate_evaluation_results(
        candidate,
        [
            event_result("CPI_2024_09_11_TIGHT", 8.0),
            event_result("FOMC_2024_09_18_TIGHT", -1.0),
            event_result("NFP_2024_10_04_TIGHT", -3.0),
        ],
        gates=gates,
    )

    assert aggregate.risk_metrics_source == "cross_event_net_pnl_chronological"
    assert aggregate.sortino == pytest.approx((4.0 / 3.0) / (5.0 ** 0.5))


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


def test_document_ingestion_doc_ids_include_resolved_path_identity(tmp_path, monkeypatch):
    import scripts.run_pipeline as run_pipeline

    doc_a = tmp_path / "thesis" / "analysis.txt"
    doc_b = tmp_path / "market_data" / "analysis.txt"
    doc_a.parent.mkdir()
    doc_b.parent.mkdir()
    doc_a.write_text("CPI thesis", encoding="utf-8")
    doc_b.write_text("Market data note", encoding="utf-8")
    doc_ids = []

    def fake_extract(path):
        return Path(path).read_text(encoding="utf-8")

    def fake_build_knowledge_graph(text, doc_id):
        doc_ids.append(doc_id)
        return {"doc_id": doc_id}

    monkeypatch.setattr(run_pipeline, "extract_text", fake_extract)
    monkeypatch.setattr(run_pipeline, "summarise_text", lambda text: "summary")
    monkeypatch.setattr(run_pipeline, "build_knowledge_graph", fake_build_knowledge_graph)
    monkeypatch.setattr(run_pipeline, "graph_to_kg_records", lambda graph: {"nodes": [], "edges": []})
    monkeypatch.setattr(run_pipeline, "persist_graph_slice", lambda *args, **kwargs: (0, 0))

    meta_a = run_pipeline.ingest_document_with_cache(
        doc_a,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )[1]
    meta_b = run_pipeline.ingest_document_with_cache(
        doc_b,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )[1]

    assert len(doc_ids) == 2
    assert doc_ids[0].startswith("doc:analysis_")
    assert doc_ids[1].startswith("doc:analysis_")
    assert doc_ids[0] != doc_ids[1]
    assert meta_a["doc_id"] == doc_ids[0]
    assert meta_b["doc_id"] == doc_ids[1]


def test_document_ingestion_rebuilds_stale_stem_only_doc_id_cache(tmp_path, monkeypatch):
    import scripts.run_pipeline as run_pipeline

    doc = tmp_path / "notes" / "analysis.txt"
    doc.parent.mkdir()
    doc.write_text("CPI note", encoding="utf-8")
    calls = {"extract": 0}

    def fake_extract(path):
        calls["extract"] += 1
        return Path(path).read_text(encoding="utf-8")

    monkeypatch.setattr(run_pipeline, "extract_text", fake_extract)
    monkeypatch.setattr(run_pipeline, "summarise_text", lambda text: "summary")
    monkeypatch.setattr(run_pipeline, "build_knowledge_graph", lambda text, doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(run_pipeline, "graph_to_kg_records", lambda graph: {"nodes": [], "edges": []})
    monkeypatch.setattr(run_pipeline, "persist_graph_slice", lambda *args, **kwargs: (0, 0))

    _, meta = run_pipeline.ingest_document_with_cache(
        doc,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )
    cache_path = Path(meta["cache_path"])
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["doc_id"] = "doc:analysis"
    cache_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")

    _, meta2 = run_pipeline.ingest_document_with_cache(
        doc,
        repo_root=tmp_path,
        cache_config={"enabled": True, "root": "cache"},
    )

    assert calls["extract"] == 2
    assert meta2["status"] == "miss"
    assert meta2["doc_id"].startswith("doc:analysis_")
    assert meta2["doc_id"] != "doc:analysis"


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


def test_run_pipeline_dry_run_honors_candidate_search_cli(tmp_path, monkeypatch, capsys):
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
        candidate_id="cand_search",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.15},
        thesis=parsed.thesis,
        metadata={"candidate_search": {"method": "evolutionary"}},
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_search_cli",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }
    captured = {}

    def fake_generate_candidates(parsed_arg, **kwargs):
        captured.update(kwargs)
        return [candidate]

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_search_cli")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", fake_generate_candidates)
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
            "--candidate-search-method",
            "evolutionary",
            "--candidate-search-seed",
            "77",
        ]
    )

    assert code == 0
    assert captured["search_method"] == "evolutionary"
    assert captured["search_seed"] == 77
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_search_cli"
    runtime_receipt = json.loads((run_dir / "pipeline_runtime_config.json").read_text(encoding="utf-8"))
    assert runtime_receipt["effective"]["candidate_search"] == {
        "method": "evolutionary",
        "seed": 77,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["candidate_id"] == "cand_search"


def test_run_pipeline_rl_required_blocks_before_candidate_generation(tmp_path, monkeypatch, capsys):
    import scripts.run_pipeline as run_pipeline

    request = {
        "schema_version": "1",
        "request_id": "pipeline_rl_blocked",
        "thesis": "Fade spread blowout after CPI",
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_rl_blocked")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(
        run_pipeline,
        "parse_hypothesis",
        lambda *args, **kwargs: pytest.fail("RL block should happen before parse"),
    )
    monkeypatch.setattr(
        run_pipeline,
        "generate_candidates",
        lambda *args, **kwargs: pytest.fail("RL block should happen before candidate generation"),
    )

    code = run_pipeline.main(
        [
            "--thesis",
            request["thesis"],
            "--event-id",
            request["event_id"],
            "--repo-root",
            str(tmp_path),
            "--no-llm",
            "--max-candidates",
            "1",
            "--rl-required",
        ]
    )

    assert code == 2
    payload = _last_json_object(capsys.readouterr().out)
    assert payload["status"] == "blocked_rl_training"
    assert payload["rl_policy_artifact"]["failure_reasons"] == ["missing_rl_feature_names"]
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_rl_blocked"
    assert (run_dir / "rl_policy_artifact.json").is_file()


def test_run_pipeline_log_handlers_are_call_local(tmp_path):
    import scripts.run_pipeline as run_pipeline

    contexts = []
    try:
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        run_a.mkdir()
        run_b.mkdir()
        log_a, handler_a, token_a = run_pipeline._configure_run_logging(run_a, "run_a")
        contexts.append((handler_a, token_a))
        run_pipeline.logger.info("a_only")

        log_b, handler_b, token_b = run_pipeline._configure_run_logging(run_b, "run_b")
        contexts.append((handler_b, token_b))
        run_pipeline.logger.info("b_only")

        assert handler_a in run_pipeline.logger.handlers
        assert handler_b in run_pipeline.logger.handlers

        run_pipeline._close_run_logging(handler_b, token_b)
        contexts.pop()
        run_pipeline.logger.info("a_after_b")
    finally:
        while contexts:
            handler, token = contexts.pop()
            run_pipeline._close_run_logging(handler, token)

    events_a = [
        json.loads(line)["event"]
        for line in log_a.read_text(encoding="utf-8").splitlines()
    ]
    events_b = [
        json.loads(line)["event"]
        for line in log_b.read_text(encoding="utf-8").splitlines()
    ]
    assert "a_only" in events_a
    assert "a_after_b" in events_a
    assert "b_only" not in events_a
    assert "b_only" in events_b
    assert "a_only" not in events_b
    assert "a_after_b" not in events_b


def test_run_pipeline_full_evaluation_orchestrator_result_uses_marker(
    tmp_path, monkeypatch, capsys
):
    import scripts.run_pipeline as run_pipeline
    from research_pipeline.types import (
        CandidateModel,
        EvaluationResult,
        GateThresholds,
        ParsedHypothesis,
    )

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
        candidate_id="cand_full_eval",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.05},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_full_marker",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }

    def fake_evaluate(cand, event_id, repo_root, **kwargs):
        return EvaluationResult(
            candidate=cand,
            event_id=event_id,
            net_pnl=1.0,
            num_trades=1,
            win_rate=1.0,
            expectancy=1.0,
            tail_loss=0.0,
            gates=kwargs.get("gates") or GateThresholds(min_trades=0),
        )

    def fake_deploy(repo_root, report):
        report.selected = candidate
        return tmp_path / "research_cards" / "pipeline_runs" / "pipeline_full_marker"

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_full_marker")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "evaluate_model", fake_evaluate)
    monkeypatch.setattr(run_pipeline, "deploy_best", fake_deploy)
    monkeypatch.setattr(
        run_pipeline,
        "build_pipeline_response",
        lambda report, request, **kwargs: {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "selected_model_id": report.selected.model_id if report.selected else None,
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
            "--no-llm",
            "--max-candidates",
            "1",
            "--orchestrator-result",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    marker_lines = [
        line
        for line in stdout.splitlines()
        if line.startswith(run_pipeline._PIPELINE_RESULT_MARKER)
    ]
    assert len(marker_lines) == 1
    slim = json.loads(marker_lines[0].removeprefix(run_pipeline._PIPELINE_RESULT_MARKER))
    assert slim == {
        "run_id": "pipeline_full_marker",
        "artifact_dir": str(tmp_path / "research_cards" / "pipeline_runs" / "pipeline_full_marker"),
        "status": "candidate_deployed",
        "paths": None,
    }
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_full_marker"
    receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "candidate_deployed"
    trials = json.loads((run_dir / "num_trials.json").read_text(encoding="utf-8"))
    assert trials["generated_candidates"] == 1
    assert trials["evaluated_candidates"] == 1
    edge_summary = json.loads((run_dir / "edge_evaluation_summary.json").read_text(encoding="utf-8"))
    assert edge_summary["schema_version"] == "hft3_edge_evaluation_summary_v1"


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


def test_run_pipeline_concurrent_failures_keep_call_local_receipts(tmp_path, monkeypatch):
    import scripts.run_pipeline as run_pipeline

    thread_state = threading.local()
    barrier = threading.Barrier(2)
    run_ids = ("pipeline_concurrent_failure_a", "pipeline_concurrent_failure_b")

    def fake_run_id():
        return thread_state.run_id

    def fake_request(**kwargs):
        return {
            "schema_version": "1",
            "request_id": kwargs["request_id"],
            "thesis": kwargs["thesis"],
            "event_id": kwargs["event_id"],
            "openfoundry_meta": {},
            "max_candidates": kwargs["max_candidates"],
        }

    def fail_after_both_runs_started(*args, **kwargs):
        barrier.wait(timeout=5)
        raise RuntimeError(f"synthetic parse failure for {thread_state.run_id}")

    def run_pipeline_thread(run_id: str) -> int:
        thread_state.run_id = run_id
        return run_pipeline.main(
            [
                "--thesis",
                f"Fade spread blowout after CPI {run_id}",
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

    monkeypatch.setattr(run_pipeline, "_run_id", fake_run_id)
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", fake_request)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", fail_after_both_runs_started)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = dict(zip(run_ids, executor.map(run_pipeline_thread, run_ids)))

    assert results == {run_id: 1 for run_id in run_ids}
    for run_id in run_ids:
        run_dir = tmp_path / "research_cards" / "pipeline_runs" / run_id
        receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
        assert receipt["run_id"] == run_id
        assert receipt["request_packet"]["request_id"] == run_id
        assert receipt["request_packet"]["thesis"] == f"Fade spread blowout after CPI {run_id}"
        assert receipt["error"] == {
            "type": "RuntimeError",
            "message": f"synthetic parse failure for {run_id}",
        }


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


def test_run_pipeline_doc_ingestion_error_keeps_document_summary_null(
    tmp_path, monkeypatch, capsys
):
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
        candidate_id="cand_doc_fail",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        strategy_params={"signal_threshold": 0.05},
        thesis=parsed.thesis,
    )
    request = {
        "schema_version": "1",
        "request_id": "pipeline_doc_fail",
        "thesis": parsed.thesis,
        "event_id": "CPI_2024_09_11_TIGHT",
        "openfoundry_meta": {},
        "max_candidates": 1,
    }
    captured = {}

    def fail_ingest(*args, **kwargs):
        raise RuntimeError("synthetic doc parse failure")

    def fake_response(report, request, **kwargs):
        captured["document_summary"] = report.document_summary
        return {
            "request_id": request["request_id"],
            "llm_status": kwargs["llm_status"],
            "parsed": {"primary_model_id": report.parsed.primary_model_id},
        }

    monkeypatch.setattr(run_pipeline, "_run_id", lambda: "pipeline_doc_fail")
    monkeypatch.setattr(run_pipeline, "build_pipeline_request", lambda **kwargs: request)
    monkeypatch.setattr(run_pipeline, "ingest_document_with_cache", fail_ingest)
    monkeypatch.setattr(run_pipeline, "parse_hypothesis", lambda *args, **kwargs: parsed)
    monkeypatch.setattr(run_pipeline, "generate_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(run_pipeline, "build_pipeline_response", fake_response)

    code = run_pipeline.main(
        [
            "--thesis",
            parsed.thesis,
            "--event-id",
            "CPI_2024_09_11_TIGHT",
            "--repo-root",
            str(tmp_path),
            "--doc",
            str(tmp_path / "paper.txt"),
            "--dry-run",
            "--no-llm",
            "--max-candidates",
            "1",
        ]
    )

    assert code == 0
    payload = _last_json_object(capsys.readouterr().out)
    run_dir = tmp_path / "research_cards" / "pipeline_runs" / "pipeline_doc_fail"
    receipt = json.loads((run_dir / "pipeline_run_receipt.json").read_text(encoding="utf-8"))
    assert captured["document_summary"] is None
    assert payload["document_summary"] is None
    assert receipt["document_summary"] is None
    assert payload["document_cache"] == {
        "status": "error",
        "error": "synthetic doc parse failure",
    }


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

    assert code == 2
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
    assert cands[0].strategy_params != cands[1].strategy_params


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
                "SECOND_WAVE_CONTINUATION",
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
                "thesis_code": "second_wave_continuation",
                "instrument_ids": ["MES"],
                "primary_model_id": "SECOND_WAVE_CONTINUATION",
                "feature_ids": ["SECOND_WAVE_CONTINUATION"],
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


def test_idea_static_filter_rejects_structural_primary_even_if_allowed():
    from research_pipeline.idea_generation import candidates_from_ideas

    packet = _idea_packet()
    packet["constraints"]["allowed_model_ids"] = ["BOOK_PRESSURE"]
    packet["ideas"] = [packet["ideas"][0]]
    packet["ideas"][0]["primary_model_id"] = "BOOK_PRESSURE"
    packet["ideas"][0]["feature_ids"] = ["BOOK_PRESSURE"]

    candidates = candidates_from_ideas(packet, max_candidates=1)

    assert candidates == []
    assert packet["ideas"][0]["status"] == "static_reject"
    assert "primary_model_id_not_hypothesis" in packet["ideas"][0]["static_error_codes"]


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
    from research_pipeline.deployment import deploy_best
    from research_pipeline.types import CandidateModel, EvaluationResult, GateThresholds, ParsedHypothesis, PipelineReport

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
    report = PipelineReport(
        run_id="no_fallback",
        thesis="x",
        event_id="CPI_2024_09_11_TIGHT",
        parsed=ParsedHypothesis(
            thesis="x",
            instrument_universe=["MES"],
            entry_rules=[],
            exit_rules=[],
            indicators=[],
            feature_list=[],
            param_ranges={},
            primary_model_id="BOOK_PRESSURE",
        ),
        candidates_tested=1,
        results=[failing],
        selected=None,
        artifact_dir=str(REPO / "research_cards" / "pipeline_runs" / "no_fallback"),
    )
    assert deploy_best(REPO, report) is None
    assert report.selected is None
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

    edge_gates = GateThresholds(
        min_psr=0.8,
        min_dsr=0.7,
        max_pbo=0.25,
        min_tail_ratio=1.0,
        max_cvar_95=5.0,
        require_sample_size=True,
        min_observations=3,
    )
    assert edge_gates.passes(
        1.0,
        3,
        0.0,
        0.5,
        psr=0.9,
        dsr=0.8,
        pbo=0.1,
        tail_ratio=1.2,
        cvar_95=2.0,
        sample_size_pass=True,
        observations=3,
    )
    assert not edge_gates.passes(1.0, 3, 0.0, 0.5, psr=0.7, dsr=0.8, pbo=0.1)
    assert not edge_gates.passes(1.0, 3, 0.0, 0.5, psr=0.9, dsr=0.6, pbo=0.1)
    assert not edge_gates.passes(1.0, 3, 0.0, 0.5, psr=0.9, dsr=0.8, pbo=0.3)
    assert not edge_gates.passes(
        1.0,
        3,
        0.0,
        0.5,
        psr=0.9,
        dsr=0.8,
        pbo=0.1,
        sample_size_pass=False,
        observations=3,
    )


def test_evaluate_model_aggregate_costs_use_reported_trade_count(tmp_path, monkeypatch):
    import sys
    import types

    import research_pipeline.evaluation as evaluation
    from research_pipeline.types import CandidateModel, GateThresholds

    class FakeEngine:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def run(self, *args, **kwargs):
            return {
                "report": {"net_pnl": 100.0, "num_trades": 4},
                "diagnostics": {"win_rate": 0.5, "expectancy": 25.0},
            }

    monkeypatch.setattr(evaluation, "resolve_model_id", lambda model_id: model_id)
    engine_mod = types.ModuleType("workbench.src.run.engine")
    engine_mod.WorkbenchEngine = FakeEngine
    monkeypatch.setitem(sys.modules, "workbench.src.run.engine", engine_mod)

    result = evaluation.evaluate_model(
        CandidateModel(candidate_id="c", model_id="BOOK_PRESSURE", strategy_params={}, thesis="x"),
        "CPI_2024_09_11_TIGHT",
        tmp_path,
        gates=GateThresholds(min_trades=0),
        cost_config={"commission_per_trade": 1.25},
    )

    assert result.num_trades == 4
    assert result.cost_breakdown["commission"] == pytest.approx(5.0)
    assert result.net_pnl == pytest.approx(95.0)


def test_evaluate_model_zero_pnl_charges_reported_trade_count_costs(tmp_path, monkeypatch):
    import sys
    import types

    import research_pipeline.evaluation as evaluation
    from research_pipeline.types import CandidateModel, GateThresholds

    class FakeEngine:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def run(self, *args, **kwargs):
            return {
                "report": {"net_pnl": 0.0, "num_trades": 4},
                "diagnostics": {"win_rate": 0.5, "expectancy": 0.0},
            }

    monkeypatch.setattr(evaluation, "resolve_model_id", lambda model_id: model_id)
    engine_mod = types.ModuleType("workbench.src.run.engine")
    engine_mod.WorkbenchEngine = FakeEngine
    monkeypatch.setitem(sys.modules, "workbench.src.run.engine", engine_mod)

    result = evaluation.evaluate_model(
        CandidateModel(candidate_id="c", model_id="BOOK_PRESSURE", strategy_params={}, thesis="x"),
        "CPI_2024_09_11_TIGHT",
        tmp_path,
        gates=GateThresholds(min_trades=0),
        cost_config={"commission_per_trade": 1.25},
    )

    assert result.num_trades == 4
    assert result.cost_breakdown["commission"] == pytest.approx(5.0)
    assert result.net_pnl == pytest.approx(-5.0)
    assert result.tail_loss == pytest.approx(-5.0)


def test_evaluate_model_no_loss_tail_ratio_passes_gate(tmp_path, monkeypatch):
    import math
    import sys
    import types

    import research_pipeline.evaluation as evaluation
    from research_pipeline.types import CandidateModel, GateThresholds

    class FakeEngine:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def run(self, *args, **kwargs):
            return {
                "report": {"net_pnl": 6.0, "num_trades": 3, "pnl_series": [1.0, 2.0, 3.0]},
                "diagnostics": {"win_rate": 1.0, "expectancy": 2.0},
            }

    monkeypatch.setattr(evaluation, "resolve_model_id", lambda model_id: model_id)
    engine_mod = types.ModuleType("workbench.src.run.engine")
    engine_mod.WorkbenchEngine = FakeEngine
    monkeypatch.setitem(sys.modules, "workbench.src.run.engine", engine_mod)

    result = evaluation.evaluate_model(
        CandidateModel(candidate_id="c", model_id="BOOK_PRESSURE", strategy_params={}, thesis="x"),
        "CPI_2024_09_11_TIGHT",
        tmp_path,
        gates=GateThresholds(min_tail_ratio=1.0),
    )

    assert result.tail_ratio == math.inf
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


def test_native_hot_path_evidence_accepts_legacy_space_sha256_format():
    from backtest_pipeline.src import hftbacktest_realism as hbt0

    digest = "a" * 64
    legacy = f"reports/cpp_lane/hft3_features_cpp_verify_cpp_parity.json sha256:{digest}"
    modern = f"reports/cpp_lane/hft3_features_cpp_verify_cpp_parity.json#sha256:{digest}"

    assert hbt0._contains_sha256_digest(modern)
    assert hbt0._contains_sha256_digest(legacy)
    assert hbt0._looks_like_native_cpp_hot_path_evidence(legacy)
    assert hbt0._native_cpp_hot_path_evidence_classes(legacy) == {"features"}
    assert not hbt0._contains_sha256_digest(f"sha256:{digest}")
    assert not hbt0._contains_sha256_digest(
        f"reports/cpp_lane/hft3_features_cpp_verify_cpp_parity.jsonsha256:{digest}"
    )


def test_strict_replay_eligible_ids_requires_applied_robustness_receipt(monkeypatch):
    import scripts.run_pipeline as run_pipeline
    from backtest_pipeline.src.hftbacktest_realism import (
        validate_applied_robustness_evidence_receipt,
    )

    def fake_replay_eligibility(row, *, screening_artifact=None):
        reasons = validate_applied_robustness_evidence_receipt(
            row,
            screening_artifact=screening_artifact,
        )
        return [f"screening_artifact_replay_ineligible:{reason}" for reason in reasons]

    monkeypatch.setattr(run_pipeline, "validate_candidate_replay_eligibility", fake_replay_eligibility)
    artifact = {
        "screening_artifact_hash": "9" * 64,
        "data_manifest_hash": "d" * 64,
        "lake_manifest_hash": "e" * 64,
        "promoted_ids": ["cand_vbt"],
        "robustness_evidence_receipt": {
            "schema": "hft3_robustness_evidence_application_receipt_v1",
            "input_screening_artifact_hash": "a" * 64,
            "robustness_evidence_schema": "hft3_robustness_evidence_inputs_v1",
            "matched_candidate_ids": ["cand_vbt"],
            "eligible_candidate_ids": ["cand_vbt"],
        },
    }
    valid_receipt = {
        "schema": "hft3_robustness_evidence_inputs_v1",
        "binding": {
            "screening_artifact_hash": "a" * 64,
            "candidate_id": "cand_vbt",
            "parameter_values_hash": "b" * 64,
            "feature_recipe_hash": "c" * 64,
            "data_manifest_hash": artifact["data_manifest_hash"],
            "lake_manifest_hash": artifact["lake_manifest_hash"],
        },
        "source_evidence": {
            "wfc_rows": "research_cards/robustness/wfc_rows.json#sha256:" + "f" * 64,
        },
        "evidence_entry_hash": "b" * 64,
    }
    artifact["robustness_evidence_receipt"]["row_receipt_hashes"] = {
        "cand_vbt": run_pipeline._canonical_hash(valid_receipt)
    }
    base_row = {
        "candidate_id": "cand_vbt",
        "parameter_values_hash": "b" * 64,
        "feature_recipe_hash": "c" * 64,
        "replay_eligibility_status": "eligible",
    }

    eligible, ineligible = run_pipeline._strict_replay_eligible_ids(
        {**artifact, "promoted": [{**base_row, "robustness_evidence_receipt": valid_receipt}]}
    )
    assert eligible == ["cand_vbt"]
    assert ineligible == {}

    invalid_receipts = [
        None,
        {},
        {"status": "pending"},
        {**valid_receipt, "binding": {"candidate_id": "other"}},
        {**valid_receipt, "binding": {**valid_receipt["binding"], "screening_artifact_hash": artifact["screening_artifact_hash"]}},
        {**valid_receipt, "binding": {**valid_receipt["binding"], "parameter_values_hash": "9" * 64}},
        {**valid_receipt, "binding": {**valid_receipt["binding"], "lake_manifest_hash": ""}},
        {**valid_receipt, "source_evidence": {"wfc_rows": {"sha256": "f" * 64}}},
        {
            **valid_receipt,
            "source_evidence": {
                "wfc_rows": {
                    "path": "research_cards/robustness/wfc_rows.json",
                    "sha256": "sha256:" + "f" * 64,
                }
            },
        },
        {**valid_receipt, "source_evidence": {"wfc_rows": "research_cards/wfc.json#sha256:not-a-digest"}},
        {**valid_receipt, "evidence_entry_hash": "not-a-digest"},
    ]
    for receipt in invalid_receipts:
        eligible, ineligible = run_pipeline._strict_replay_eligible_ids(
            {**artifact, "promoted": [{**base_row, "robustness_evidence_receipt": receipt}]}
        )
        assert eligible == []
        assert "cand_vbt" in ineligible
        assert ineligible["cand_vbt"] != ["robustness_evidence_receipt_missing"]
        assert all(
            reason.startswith("screening_artifact_replay_ineligible:")
            for reason in ineligible["cand_vbt"]
        )

    invalid_artifacts = [
        {key: value for key, value in artifact.items() if key != "robustness_evidence_receipt"},
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "input_screening_artifact_hash": "8" * 64,
            },
        },
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "input_screening_artifact_hash": "sha256:" + artifact["screening_artifact_hash"],
            },
        },
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "robustness_evidence_schema": "wrong_schema",
            },
        },
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "matched_candidate_ids": [],
            },
        },
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "eligible_candidate_ids": [],
            },
        },
        {
            **artifact,
            "robustness_evidence_receipt": {
                **artifact["robustness_evidence_receipt"],
                "row_receipt_hashes": {"cand_vbt": "0" * 64},
            },
        },
    ]
    for invalid_artifact in invalid_artifacts:
        eligible, ineligible = run_pipeline._strict_replay_eligible_ids(
            {
                **invalid_artifact,
                "promoted": [{**base_row, "robustness_evidence_receipt": valid_receipt}],
            }
        )
        assert eligible == []
        assert "cand_vbt" in ineligible
        assert ineligible["cand_vbt"] != ["robustness_evidence_receipt_missing"]
        assert all(
            reason.startswith("screening_artifact_replay_ineligible:")
            for reason in ineligible["cand_vbt"]
        )


def test_strict_replay_eligible_ids_passes_screening_artifact_to_validator(monkeypatch):
    import scripts.run_pipeline as run_pipeline

    artifact = {
        "promoted_ids": ["cand_vbt"],
        "promoted": [{"candidate_id": "cand_vbt"}],
    }

    def fake_replay_eligibility(row, *, screening_artifact=None):
        assert screening_artifact is artifact
        return ["screening_artifact_replay_ineligible:robustness_evidence_receipt_candidate_mismatch"]

    monkeypatch.setattr(run_pipeline, "validate_candidate_replay_eligibility", fake_replay_eligibility)

    eligible, ineligible = run_pipeline._strict_replay_eligible_ids(artifact)

    assert eligible == []
    assert ineligible == {
        "cand_vbt": [
            "screening_artifact_replay_ineligible:robustness_evidence_receipt_candidate_mismatch"
        ]
    }


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
