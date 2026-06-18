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
from research_pipeline.types import ParsedHypothesis


@dataclass
class _FakeFilterResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _fake_filter(*, candidates, parsed, event_id, repo_root, gates, screening_scope, run_budget=None, **kwargs):
    promoted = []
    for cand in candidates[:2]:
        promoted.append(
            {
                "candidate_id": cand.candidate_id,
                "hypothesis_id": cand.model_id,
                "param_values": dict(cand.strategy_params),
                "vectorbt_results": {"oos_expectancy": 1.0, "max_drawdown_pct": -5.0},
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
                "metrics": {"holdout_eval": {"net_return": 999}, "discovery": {"net_return": 1.0}},
            }
        ],
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
        return {"robustness_pass": True, "metrics": {"oos_expectancy": 1.0}, "campaign_id": "rob1"}

    def hft_fn(**kwargs):
        hft_calls.append(1)
        mock = MagicMock()
        mock.status = "pass"
        mock.summary = {"status": "pass"}
        return mock

    cfg = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=3,
        exploration_fraction=0.0,
        family_search_enabled=False,
        run_robustness=True,
        run_hft_campaign=True,
        hft_source_npz=tmp_path / "x.npz",
        hft_latency_model=tmp_path / "lat.json",
        hft_fill_queue_model=tmp_path / "fill.json",
    )
    for name in ("x.npz", "lat.json", "fill.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "research_pipeline.generation_loop.generate_scenario_manifest",
        lambda cfg: ([], []),
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
    assert code == 0
    assert len(filter_calls) == 2
    assert len(robustness_calls) == 4
    assert len(hft_calls) == 2
    assert report["generations_run"] == 2
    manifest = load_manifest(tmp_path, report["campaign_id"])
    assert manifest["tested_parameter_hashes"]


def test_resume_preserves_manifest_hash(tmp_path: Path) -> None:
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

    cfg2 = AutoresearchConfig(max_generations=2, max_candidates_per_generation=2, run_robustness=False)
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
    summary = {"campaign_id": "c1", "best_candidate_id": "x", "best_composite_score": 1.0, "candidates": []}
    path = append_generation_memory(tmp_path, summary, generation_index=0)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["authority"] == "advisory"
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
    cfg2 = AutoresearchConfig(max_generations=2, max_candidates_per_generation=2, run_robustness=False)
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
            json.dumps({"wfc_status": "PASS", "robustness_passed": True, "metrics": {}}),
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
