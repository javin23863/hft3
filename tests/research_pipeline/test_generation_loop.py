"""Autoresearch generation loop tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.generation_loop import AutoresearchConfig, load_autoresearch_config, run_autoresearch_loop
from research_pipeline.generation_state import load_manifest
from research_pipeline.generation_summary import build_generation_summary
from research_pipeline.idea_generation import parsed_from_idea
from research_pipeline.review_memory import append_generation_memory, load_tested_hashes
from research_pipeline.types import CandidateModel, ParsedHypothesis


@pytest.fixture(autouse=True)
def _pass_ontology_gate(monkeypatch):
    from research_pipeline.generation_gate_chain import GATE_ONTOLOGY, build_gate_receipt
    from research_pipeline import generation_loop as gl

    def _pass_ontology(*, manifest, repo_root):
        return build_gate_receipt(
            gate_id=GATE_ONTOLOGY,
            gate_version="1.0.0",
            candidate_id=str(manifest["candidate_id"]),
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest.get("manifest_hash") or "pending"),
            status="PASS",
            required_checks=["fable_entry_checklist", "citation_trace"],
            required_check_count=2,
            passed_check_count=2,
        )

    monkeypatch.setattr(gl, "run_ontology_gate_for_candidate", _pass_ontology)


@dataclass
class _FakeFilterResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _fresh_proposal_candidates(**kwargs):
    """Return dedup-safe candidates for multi-generation loop tests."""
    from research_pipeline.model_generation import generate_candidates

    parsed = kwargs["parsed"]
    gen_idx = len(kwargs.get("tested_hashes") or [])
    out = generate_candidates(
        parsed,
        max_candidates=int(kwargs.get("max_candidates") or 3),
        expand_for_vectorbt=True,
        target_event_id=kwargs.get("target_event_id"),
        target_symbol=kwargs.get("target_symbol"),
    )
    fresh: list = []
    for i, cand in enumerate(out):
        params = dict(cand.strategy_params)
        params["_test_gen_probe"] = gen_idx + i
        fresh.append(
            CandidateModel(
                candidate_id=f"{cand.candidate_id}_g{gen_idx}_{i}",
                model_id=cand.model_id,
                strategy_params=params,
                thesis=cand.thesis,
                metadata=dict(cand.metadata),
            )
        )
    return fresh[: int(kwargs.get("max_candidates") or 3)]


def _passing_surface_metrics() -> dict[str, Any]:
    from backtest_pipeline.src.surface_stability import compute_surface_stability

    grid = {
        (r, c): {"net_return": 0.10, "trade_count": 50}
        for r in range(3)
        for c in range(3)
    }
    return compute_surface_stability(grid)


def _serializable_statistical_evidence() -> dict[str, Any]:
    passed = {"status": "pass"}
    return {
        "robustness_artifact_staleness": "fresh",
        "dsr_status": "pass",
        "pbo_status": "pass",
        "cscv_status": "pass",
        "bootstrap_ci_or_not_run": passed,
        "dsr_or_not_run": passed,
        "pbo_or_not_run": passed,
        "cscv_count_or_not_run": passed,
        "fee_stress_or_not_run": passed,
        "slippage_stress_or_not_run": passed,
        "latency_stress_or_not_run": passed,
        "holm_stepdown_or_not_run": passed,
        "holm_bh_or_not_run": passed,
        "null_battery_or_not_run": passed,
        "planted_alpha_or_not_run": passed,
        "adversarial_or_not_run": passed,
        "parameter_perturbation_or_not_run": passed,
    }


def _fake_filter(*, candidates, parsed, event_id, repo_root, gates, screening_scope, run_budget=None, **kwargs):
    promoted = []
    surface = _passing_surface_metrics()
    statistical = _serializable_statistical_evidence()
    for cand in candidates[:2]:
        vectorbt_results = {
            "oos_expectancy": 1.0,
            "max_drawdown_pct": -5.0,
            "num_trades": 50,
            "hit_rate": 0.55,
            "gross_return": 0.12,
            "net_return": 0.10,
            "net_pnl": 1000.0,
            "total_fees": 50.0,
            "total_slippage": 25.0,
            "trade_count": 50,
            "expectancy_per_trade": 0.02,
            "profit_factor": 1.4,
            "sharpe": 0.8,
            "sortino": 1.1,
            "max_drawdown": -0.05,
            "turnover": 0.3,
            "surface_stability_metrics": surface,
            **statistical,
        }
        promoted.append(
            {
                "candidate_id": cand.candidate_id,
                "hypothesis_id": cand.model_id,
                "param_values": dict(cand.strategy_params),
                "vectorbt_results": vectorbt_results,
                "surface_stability_metrics": surface,
                "gross_return": 0.12,
                "net_return": 0.10,
                "net_pnl": 1000.0,
                "total_fees": 50.0,
                "total_slippage": 25.0,
                "trade_count": 50,
                "hit_rate": 0.55,
                "expectancy_per_trade": 0.02,
                "profit_factor": 1.4,
                "sharpe": 0.8,
                "sortino": 1.1,
                "max_drawdown": -0.05,
                "turnover": 0.3,
                **statistical,
            }
        )
    return _FakeFilterResult(
        {
            "screening_backend": "vectorbt",
            "vectorbt_engine": "rust",
            "rust_engine_available": True,
            "rust_engine_required_for_scope": False,
            "screening_scope": screening_scope,
            "research_clock": "discovery",
            "event_id": event_id,
            "promoted": promoted,
            "promoted_ids": [p["candidate_id"] for p in promoted],
            "rejected": [],
            "feature_plane_status": "scheduled_event_only",
        }
    )


def _fake_persist(artifact, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = dict(artifact)
    artifact.setdefault("screening_artifact_hash", "abc123")
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


def test_parsed_from_idea_honors_param_ranges() -> None:
    parsed = parsed_from_idea(
        {
            "primary_model_id": "HYP_5",
            "param_ranges": {"signal_threshold": [0.2, 0.4]},
            "thesis_code": "test",
        }
    )
    assert parsed.param_ranges["signal_threshold"] == [0.2, 0.4]


def test_elite_refinement_dedup_and_neighbors() -> None:
    parsed = ParsedHypothesis(
        thesis="t",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["HYP_5"],
        param_ranges={"signal_threshold": [0.1, 0.3]},
        primary_model_id="HYP_5",
        source="heuristic",
    )
    summary = {
        "candidates": [
            {
                "elite": True,
                "candidate_id": "c1",
                "model_id": "HYP_5",
                "strategy_params": {"signal_threshold": 0.15, "holding_period_bars": 15},
            }
        ]
    }
    tested = {"deadbeefdeadbeef"}
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes=tested,
        max_candidates=5,
        exploration_fraction=0.0,
        family_search_enabled=False,
    )
    assert out
    assert all(c.candidate_id != "deadbeefdeadbeef" for c in out)


def test_generation_summary_excludes_holdout_periods(tmp_path: Path) -> None:
    wf = tmp_path / "apps" / "workbench" / "config"
    wf.mkdir(parents=True)
    (wf / "walk_forward.yaml").write_text("holdout_evaluate_only:\n  - holdout_eval\n", encoding="utf-8")
    screening = {
        "promoted": [
            {
                "candidate_id": "c1",
                "hypothesis_id": "HYP_5",
                "param_values": {"signal_threshold": 0.15},
                "vectorbt_results": {"oos_expectancy": 2.0},
            }
        ]
    }
    summary = build_generation_summary(
        repo_root=tmp_path,
        campaign_id="camp1",
        generation_index=0,
        screening_artifact=screening,
        robustness_results=[
            {
                "candidate_id": "c1",
                "robustness_pass": True,
                "regular_walk_forward_pass": True,
                "wfc_pass": True,
                "metrics": {"holdout_eval": {"net_return": 999}, "discovery": {"net_return": 1.0}},
            }
        ],
        gate_chain_by_id={"c1": {"final_pass": True, "final_status": "FINAL_PASS"}},
    )
    row = summary["candidates"][0]
    assert "holdout_eval" not in row["metrics"]


def test_generation_loop_spies_runners(tmp_path: Path, monkeypatch) -> None:
    filter_calls: list[int] = []
    robustness_calls: list[int] = []
    hft_calls: list[int] = []

    def filter_fn(**kwargs):
        filter_calls.append(1)
        return _fake_filter(**kwargs)

    def persist_fn(artifact, path):
        return _fake_persist(artifact, path)

    def robustness_fn(**kwargs):
        robustness_calls.append(1)
        out = tmp_path / f"rob_{len(robustness_calls)}"
        out.mkdir(parents=True, exist_ok=True)
        campaign_summary = {
            "status": "PASS",
            "wfc_status": "PASS",
            "robustness_passed": True,
            "periods": [{"gate_pass": True}],
            "wfc": {"pearson": 0.5, "spearman": 0.4, "wfc_status": "PASS"},
            "metrics": {},
        }
        (out / "summary.json").write_text(json.dumps(campaign_summary), encoding="utf-8")
        return {
            "robustness_pass": True,
            "regular_walk_forward_pass": True,
            "wfc_pass": True,
            "metrics": {"oos_expectancy": 1.0},
            "campaign_id": f"rob{len(robustness_calls)}",
            "campaign_summary": campaign_summary,
            "artifact_dir": str(out),
        }

    def hft_fn(**kwargs):
        from types import SimpleNamespace
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        hft_calls.append(1)
        scenarios = kwargs.get("scenarios") or []
        config = kwargs.get("config")
        hft_out = tmp_path / f"hft_{len(hft_calls)}"
        hft_out.mkdir(parents=True, exist_ok=True)
        rob_dirs = sorted(tmp_path.glob("rob*"), key=lambda p: p.stat().st_mtime, reverse=True)
        rob_hash = "rob-smoke-fallback"
        for rob_dir in rob_dirs:
            summary_path = rob_dir / "summary.json"
            if summary_path.is_file():
                rob_hash = sha256_hex(json.loads(summary_path.read_text(encoding="utf-8")))
                break
        manifests_path = (
            Path(config.out_dir).parent / "candidate_manifests.jsonl" if config else None
        )
        manifest_by_cid: dict[str, dict[str, Any]] = {}
        if manifests_path and manifests_path.is_file():
            for line in manifests_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    manifest_by_cid[str(row.get("candidate_id"))] = row
        scenario_results = []
        for i, s in enumerate(scenarios):
            cid = str(getattr(s, "candidate_id", f"c{i}"))
            manifest = manifest_by_cid.get(cid, {})
            scenario_results.append(
                SimpleNamespace(
                    scenario_id=str(getattr(s, "scenario_id", f"s{i}")),
                    status="completed",
                    replay_result={
                        "candidate_id": cid,
                        "manifest_hash": str(manifest.get("manifest_hash") or ""),
                        "feature_recipe_hash": str(manifest.get("feature_recipe_hash") or ""),
                        "screening_artifact_hash": "abc123",
                        "robustness_artifact_hash": rob_hash,
                        "certification_status": "full_fidelity_declared",
                    },
                    artifact_dir=str(hft_out),
                )
            )
        mock = MagicMock()
        mock.status = "pass"
        mock.summary = {"status": "pass"}
        mock.scenario_results = scenario_results
        return mock

    cfg = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=3,
        exploration_fraction=0.5,
        family_search_enabled=False,
        run_robustness=True,
        run_hft_campaign=True,
        hft_source_npz=tmp_path / "x.npz",
        hft_latency_model=tmp_path / "lat.json",
        hft_fill_queue_model=tmp_path / "fill.json",
    )
    for name in ("x.npz", "lat.json", "fill.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    scenario_store: dict[str, list[Any]] = {"scenarios": []}

    def fake_generate_scenario_manifest(cfg):
        scenarios = [
            type(
                "Scenario",
                (),
                {
                    "scenario_id": f"sc_{cid}",
                    "candidate_id": cid,
                    "to_dict": lambda self, _cid=cid: {"scenario_id": f"sc_{cid}", "candidate_id": _cid},
                },
            )()
            for cid in (getattr(cfg, "candidate_ids", None) or ["unknown"])
        ]
        scenario_store["scenarios"] = scenarios
        return scenarios, []

    monkeypatch.setattr(
        "research_pipeline.generation_loop.generate_scenario_manifest",
        fake_generate_scenario_manifest,
    )
    monkeypatch.setattr(
        "research_pipeline.generation_loop.load_scenarios_from_manifest",
        lambda _path: scenario_store["scenarios"],
    )
    monkeypatch.setattr(
        "research_pipeline.generation_loop.propose_next_candidates",
        _fresh_proposal_candidates,
    )

    code, report = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade blowout",
        event_id="CPI_2024_09_11_TIGHT",
        cfg=cfg,
        no_llm=True,
        filter_fn=filter_fn,
        persist_fn=persist_fn,
        robustness_fn=robustness_fn,
        hft_fn=hft_fn,
    )
    assert code == 0, report
    assert len(filter_calls) == 2
    assert len(robustness_calls) == 4
    assert len(hft_calls) == 2
    assert report["generations_run"] == 2
    manifest = load_manifest(tmp_path, report["campaign_id"])
    assert manifest["tested_parameter_hashes"]


def test_resume_preserves_manifest_hash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "research_pipeline.generation_loop.propose_next_candidates",
        _fresh_proposal_candidates,
    )
    cfg = AutoresearchConfig(max_generations=1, max_candidates_per_generation=2, run_robustness=False)
    code1, report1 = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade",
        event_id="E1",
        cfg=cfg,
        no_llm=True,
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    assert code1 == 0
    campaign_id = report1["campaign_id"]
    manifest_before = load_manifest(tmp_path, campaign_id)
    hashes_before = list(manifest_before["tested_parameter_hashes"])

    cfg2 = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=2,
        run_robustness=False,
    )
    code2, report2 = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade",
        event_id="E1",
        cfg=cfg2,
        campaign_id=campaign_id,
        resume=True,
        no_llm=True,
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    assert code2 == 0
    manifest_after = load_manifest(tmp_path, campaign_id)
    assert manifest_after["tested_parameter_hashes"][: len(hashes_before)] == hashes_before


def test_append_generation_memory_advisory_only(tmp_path: Path) -> None:
    summary = {
        "campaign_id": "c1",
        "best_candidate_id": "x",
        "best_composite_score": 1.0,
        "candidates": [
            {
                "candidate_id": "x",
                "final_status": "FINAL_PASS",
                "gate_statuses": {"ontology_gate": "PASS"},
                "research_score": 1.0,
            }
        ],
    }
    path = append_generation_memory(tmp_path, summary, generation_index=0)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    assert lines[0]["authority"] == "advisory"
    assert any(row.get("candidate_id") == "x" and row.get("authority") == "advisory" for row in lines)
    assert load_tested_hashes(tmp_path, "missing") == set()


def test_failure_stop_reason_exits_nonzero(tmp_path: Path) -> None:
    cfg = AutoresearchConfig(max_generations=1, max_candidates_per_generation=2, run_robustness=False)
    code, report = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade",
        event_id="E1",
        cfg=cfg,
        no_llm=True,
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    assert code == 0
    campaign_id = report["campaign_id"]
    prior = (
        tmp_path
        / "research_cards"
        / "autoresearch"
        / campaign_id
        / "generation_000"
        / "generation_summary.json"
    )
    prior.unlink()
    cfg2 = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=2,
        run_robustness=False,
    )
    code2, report2 = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade",
        event_id="E1",
        cfg=cfg2,
        campaign_id=campaign_id,
        resume=True,
        no_llm=True,
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    assert code2 == 1
    assert report2["stop_reason"] == "prior_generation_summary_missing"


def test_robustness_fn_forwards_frozen_params(tmp_path: Path, monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_run_campaign(*args, **kwargs):
        captured.append(dict(kwargs))
        from workbench.src.run.campaign_runner import CampaignResult

        out = tmp_path / "rob_campaign"
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "wfc_status": "PASS",
                    "robustness_passed": True,
                    "periods": [{"gate_pass": True}],
                    "wfc": {"pearson": 0.5, "spearman": 0.4, "wfc_status": "PASS"},
                    "metrics": {},
                }
            ),
            encoding="utf-8",
        )
        return CampaignResult(
            campaign_id=str(kwargs.get("campaign_id") or "rob"),
            model_id="HYP_5",
            symbol="MES",
            status="completed",
            param_hash="abc",
            artifact_dir=str(out),
        )

    monkeypatch.setattr("workbench.src.run.campaign_runner.run_campaign", fake_run_campaign)
    from research_pipeline.generation_loop import make_default_robustness_fn

    fn = make_default_robustness_fn()
    params = {"signal_threshold": 0.22, "holding_period_bars": 30}
    outcome = fn(
        repo_root=tmp_path,
        model_id="HYP_5",
        symbol="MES",
        campaign_id="rob_test",
        param_values=params,
        screening_metrics={"oos_expectancy": 1.0},
    )
    assert captured
    assert captured[0]["frozen_strategy_params"] == params
    assert outcome["robustness_pass"] is True


def test_load_autoresearch_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("max_generations: 7\nsymbol: MNQ\n", encoding="utf-8")
    cfg = load_autoresearch_config(cfg_path)
    assert cfg.max_generations == 7
    assert cfg.symbol == "MNQ"
