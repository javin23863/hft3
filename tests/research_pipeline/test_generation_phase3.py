"""Phase 3 tests: generation summary, learning memory, exploitation/exploration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, compute_feature_recipe_hash
from research_pipeline.generation_gate_chain import FINAL_ONTOLOGY_REJECTED, FINAL_PASS, FINAL_WFC_REJECTED
from research_pipeline.generation_summary import build_generation_summary
from research_pipeline.review_memory import append_generation_memory
from research_pipeline.types import CandidateModel, ParsedHypothesis


def _parsed() -> ParsedHypothesis:
    return ParsedHypothesis(
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


def test_generation_summary_includes_all_proposed_candidates(tmp_path: Path) -> None:
    screening = {
        "promoted": [
            {
                "candidate_id": "pass_vbt",
                "hypothesis_id": "HYP_5",
                "param_values": {"signal_threshold": 0.15},
                "vectorbt_results": {"oos_expectancy": 1.0},
            }
        ],
        "rejected": [{"candidate_id": "fail_vbt"}],
    }
    summary = build_generation_summary(
        repo_root=tmp_path,
        campaign_id="camp",
        generation_index=0,
        screening_artifact=screening,
        proposed_candidate_ids=["pass_vbt", "fail_vbt", "fail_ontology"],
        manifests_by_id={
            "pass_vbt": {
                "candidate_id": "pass_vbt",
                "feature_recipe_hash": "rh1",
                "manifest_hash": "mh1",
                "model_id": "HYP_5",
                "execution_assumptions": {"signal_threshold": 0.15},
            },
            "fail_vbt": {
                "candidate_id": "fail_vbt",
                "feature_recipe_hash": "rh2",
                "manifest_hash": "mh2",
                "model_id": "HYP_5",
                "execution_assumptions": {"signal_threshold": 0.2},
            },
        },
        ontology_receipts_by_id={
            "fail_ontology": {
                "status": "REJECT",
                "failure_reasons": ["ontology_unbacked_claim"],
            }
        },
        gate_chain_by_id={
            "pass_vbt": {"final_status": FINAL_PASS, "gate_outcomes": []},
            "fail_vbt": {"final_status": "VECTORBT_REJECTED", "failure_reasons": ["vectorbt_screen_reject"], "gate_outcomes": []},
        },
    )
    ids = {row["candidate_id"] for row in summary["candidates"]}
    assert ids == {"pass_vbt", "fail_vbt", "fail_ontology"}
    by_id = {row["candidate_id"]: row for row in summary["candidates"]}
    assert by_id["fail_ontology"]["final_status"] == FINAL_ONTOLOGY_REJECTED
    assert by_id["pass_vbt"]["elite"] is True
    assert by_id["fail_vbt"]["elite"] is False
    assert summary["best_candidate_id"] == "pass_vbt"
    assert summary["proposed_candidate_count"] == 3


def test_holdout_cannot_enter_proposal_memory(tmp_path: Path) -> None:
    wf = tmp_path / "apps" / "workbench" / "config"
    wf.mkdir(parents=True)
    (wf / "walk_forward.yaml").write_text("holdout_evaluate_only:\n  - holdout_eval\n", encoding="utf-8")
    summary = build_generation_summary(
        repo_root=tmp_path,
        campaign_id="c1",
        generation_index=0,
        screening_artifact={
            "promoted": [
                {
                    "candidate_id": "c1",
                    "hypothesis_id": "HYP_5",
                    "vectorbt_results": {"oos_expectancy": 1.0, "holdout_eval": {"net_return": 999}},
                }
            ]
        },
        robustness_results=[
            {
                "candidate_id": "c1",
                "regular_walk_forward_pass": True,
                "wfc_pass": True,
                "metrics": {"holdout_eval": {"net_return": 888, "evaluate_only": True}},
                "campaign_summary": {"wfc": {"pearson": 0.5, "spearman": 0.4}},
            }
        ],
        proposed_candidate_ids=["c1"],
        gate_chain_by_id={"c1": {"final_status": FINAL_PASS, "gate_outcomes": []}},
    )
    path = append_generation_memory(tmp_path, summary, generation_index=0)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
    candidate_rows = [row for row in lines if row.get("candidate_id") == "c1"]
    assert candidate_rows
    blob = json.dumps(candidate_rows[0])
    assert "holdout_eval" not in blob
    assert "888" not in blob
    assert "999" not in blob
    assert candidate_rows[0]["walk_forward_correlation_pearson"] == 0.5
    assert candidate_rows[0]["authority"] == "advisory"


def test_duplicate_recipe_not_retested() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="c1",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="t",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    recipe_hash = str(attached.feature_recipe_hash)
    summary = {
        "candidates": [
            {
                "elite": True,
                "final_status": FINAL_PASS,
                "candidate_id": "c1",
                "model_id": "HYP_5",
                "strategy_params": dict(attached.strategy_params),
                "feature_recipe": dict(attached.feature_recipe or {}),
                "feature_recipe_hash": recipe_hash,
            }
        ]
    }
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes={recipe_hash},
        max_candidates=10,
        exploration_fraction=0.0,
        target_event_id="CPI_2024_09_11_TIGHT",
        family_search_enabled=True,
        family_search_fraction=1.0,
    )
    assert all(c.feature_recipe_hash != recipe_hash for c in out if c.feature_recipe_hash)


def test_child_changes_recipe_dimension() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="c1",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="t",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    base_hash = str(attached.feature_recipe_hash)
    summary = {
        "candidates": [
            {
                "elite": True,
                "final_status": FINAL_PASS,
                "candidate_id": "c1",
                "model_id": "HYP_5",
                "strategy_params": dict(attached.strategy_params),
                "feature_recipe": dict(attached.feature_recipe or {}),
                "feature_recipe_hash": base_hash,
            }
        ]
    }
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes={base_hash},
        max_candidates=8,
        exploration_fraction=0.0,
        target_event_id="CPI_2024_09_11_TIGHT",
        family_search_enabled=True,
        family_search_fraction=0.5,
    )
    family_children = [c for c in out if c.metadata.get("refinement") == "family_variant"]
    assert family_children
    for child in family_children:
        assert child.feature_recipe_hash != base_hash
        assert compute_feature_recipe_hash(dict(child.feature_recipe or {})) == child.feature_recipe_hash


def test_failure_driven_skips_blocked_wfc_family() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="seed",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="t",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    summary = {
        "candidates": [
            {
                "elite": False,
                "final_status": FINAL_WFC_REJECTED,
                "candidate_id": "seed",
                "model_id": "HYP_5",
                "strategy_params": dict(attached.strategy_params),
                "feature_recipe": dict(attached.feature_recipe or {}),
                "feature_recipe_hash": attached.feature_recipe_hash,
                "feature_family_mutation": "vix_sensor_declared",
                "rejection_reasons": ["wfc_status=REJECT"],
            }
        ]
    }
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes=set(),
        max_candidates=6,
        exploration_fraction=0.0,
        family_search_enabled=False,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    assert out
    assert all(c.metadata.get("family_variant_id") != "vix_sensor_declared" for c in out)
