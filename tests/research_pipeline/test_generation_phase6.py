"""Phase 6 — expanded planted PASS/FAIL gate tests (assignment §20)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_pipeline.elite_refinement import propose_next_candidates
from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate, compute_feature_recipe_hash
from research_pipeline.generation_gate_chain import FINAL_PASS, FINAL_WFC_REJECTED
from research_pipeline.generation_gate_producers import build_statistical_robustness_gate_receipt
from research_pipeline.generation_loop import AutoresearchConfig, run_autoresearch_loop
from research_pipeline.generation_state import generation_dir
from research_pipeline.generation_summary import validate_generation_completion
from research_pipeline.types import CandidateModel, ParsedHypothesis
from tests.research_pipeline.test_generation_gate_integration import (
    _manifest,
    _passing_promoted_row,
    _passing_vectorbt_row,
)


def _parsed() -> ParsedHypothesis:
    return ParsedHypothesis(
        thesis="fade blowout",
        instrument_universe=["MES"],
        entry_rules=[],
        exit_rules=[],
        indicators=[],
        feature_list=["HYP_5"],
        param_ranges={"signal_threshold": [0.1, 0.3]},
        primary_model_id="HYP_5",
        source="heuristic",
    )


def test_statistical_gate_rejects_missing_bootstrap() -> None:
    row = _passing_vectorbt_row()
    row.pop("vectorbt_results", None)
    row["bootstrap_ci_or_not_run"] = {"status": "not_run"}
    row["dsr_or_not_run"] = {"status": "pass"}
    row["pbo_or_not_run"] = {"status": "pass"}
    row["cscv_count_or_not_run"] = {"status": "pass"}
    row["dsr_status"] = "pass"
    row["pbo_status"] = "pass"
    row["cscv_status"] = "pass"
    row["robustness_artifact_staleness"] = "fresh"
    row["fee_stress_or_not_run"] = {"status": "pass"}
    row["slippage_stress_or_not_run"] = {"status": "pass"}
    row["latency_stress_or_not_run"] = {"status": "pass"}
    row["holm_stepdown_or_not_run"] = {"status": "pass"}
    row["holm_bh_or_not_run"] = {"status": "pass"}
    row["null_battery_or_not_run"] = {"status": "pass"}
    row["planted_alpha_or_not_run"] = {"status": "pass"}
    row["adversarial_or_not_run"] = {"status": "pass"}
    row["parameter_perturbation_or_not_run"] = {"status": "pass"}
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    assert any("bootstrap" in str(r).lower() for r in receipt.get("failure_reasons", []))


def test_statistical_gate_rejects_fail_dsr() -> None:
    from tests.backtest_pipeline.test_robustness_bridge import _failing_dsr_expectancies

    row = _passing_promoted_row()
    row["vectorbt_results"]["robustness_input"]["per_event_expectancies"] = _failing_dsr_expectancies()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    assert any("dsr" in str(r).lower() for r in receipt.get("failure_reasons", []))


def test_statistical_gate_rejects_fail_pbo() -> None:
    from tests.backtest_pipeline.test_robustness_bridge import _failing_pbo_matrix

    row = _passing_promoted_row()
    row["vectorbt_results"]["robustness_input"]["cscv_matrix"] = _failing_pbo_matrix()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    assert any("pbo" in str(r).lower() for r in receipt.get("failure_reasons", []))


def test_statistical_gate_rejects_fail_cscv_status() -> None:
    row = _passing_vectorbt_row()
    row.pop("vectorbt_results", None)
    row["bootstrap_ci_or_not_run"] = {"status": "pass"}
    row["dsr_or_not_run"] = {"status": "pass"}
    row["pbo_or_not_run"] = {"status": "fail", "pbo_pass": False}
    row["cscv_count_or_not_run"] = {"status": "pass"}
    row["dsr_status"] = "pass"
    row["pbo_status"] = "fail"
    row["cscv_status"] = "fail"
    row["robustness_artifact_staleness"] = "fresh"
    row["fee_stress_or_not_run"] = {"status": "pass"}
    row["slippage_stress_or_not_run"] = {"status": "pass"}
    row["latency_stress_or_not_run"] = {"status": "pass"}
    row["holm_stepdown_or_not_run"] = {"status": "pass"}
    row["holm_bh_or_not_run"] = {"status": "pass"}
    row["null_battery_or_not_run"] = {"status": "pass"}
    row["planted_alpha_or_not_run"] = {"status": "pass"}
    row["adversarial_or_not_run"] = {"status": "pass"}
    row["parameter_perturbation_or_not_run"] = {"status": "pass"}
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    assert any("pbo" in str(r).lower() for r in receipt.get("failure_reasons", []))


def test_statistical_gate_rejects_structure_ran_cscv_status() -> None:
    row = _passing_vectorbt_row()
    row.pop("vectorbt_results", None)
    row["bootstrap_ci_or_not_run"] = {"status": "pass"}
    row["dsr_or_not_run"] = {"status": "pass"}
    row["pbo_or_not_run"] = {"status": "fail", "pbo_pass": False}
    row["cscv_count_or_not_run"] = {"status": "pass"}
    row["dsr_status"] = "pass"
    row["pbo_status"] = "fail"
    row["cscv_status"] = "structure_ran"
    row["robustness_artifact_staleness"] = "fresh"
    row["fee_stress_or_not_run"] = {"status": "pass"}
    row["slippage_stress_or_not_run"] = {"status": "pass"}
    row["latency_stress_or_not_run"] = {"status": "pass"}
    row["holm_stepdown_or_not_run"] = {"status": "pass"}
    row["holm_bh_or_not_run"] = {"status": "pass"}
    row["null_battery_or_not_run"] = {"status": "pass"}
    row["planted_alpha_or_not_run"] = {"status": "pass"}
    row["adversarial_or_not_run"] = {"status": "pass"}
    row["parameter_perturbation_or_not_run"] = {"status": "pass"}
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    assert any("cscv_status=structure_ran" in str(r) for r in receipt.get("failure_reasons", []))


def test_statistical_gate_rejects_missing_holm() -> None:
    row = _passing_promoted_row()
    inp = dict(row["vectorbt_results"]["robustness_input"])
    inp.pop("p_values", None)
    row["vectorbt_results"]["robustness_input"] = inp
    row["vectorbt_results"]["holm_stepdown_or_not_run"] = {"status": "not_run"}
    row["vectorbt_results"]["holm_bh_or_not_run"] = {"status": "not_run"}
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"
    failures = receipt.get("failure_reasons") or []
    assert any("holm" in str(r).lower() for r in failures)


def test_statistical_gate_pass_requires_full_gauntlet() -> None:
    row = _passing_promoted_row()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "PASS"
    assert receipt["passed_check_count"] == receipt["required_check_count"]
    evidence_keys = (
        "bootstrap_ci",
        "deflated_sharpe_ratio",
        "cscv_pbo",
        "holm_stepdown",
        "holm_bh",
    )
    for key in evidence_keys:
        payload = receipt.get(key) or {}
        assert isinstance(payload, dict)
        assert payload.get("status") == "pass", key


def test_generation_n_plus_1_uses_validated_elites_only() -> None:
    parsed = _parsed()
    elite = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="elite",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="fade",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    wfc_reject = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="wfc_fail",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.25, "holding_period_bars": 30},
            thesis="fade",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    summary = {
        "candidates": [
            {
                "elite": True,
                "final_status": FINAL_PASS,
                "candidate_id": "elite",
                "model_id": "HYP_5",
                "strategy_params": dict(elite.strategy_params),
                "feature_recipe": dict(elite.feature_recipe or {}),
                "feature_recipe_hash": elite.feature_recipe_hash,
            },
            {
                "elite": False,
                "final_status": FINAL_WFC_REJECTED,
                "candidate_id": "wfc_fail",
                "model_id": "HYP_5",
                "strategy_params": dict(wfc_reject.strategy_params),
                "feature_recipe": dict(wfc_reject.feature_recipe or {}),
                "feature_recipe_hash": wfc_reject.feature_recipe_hash,
            },
        ]
    }
    out = propose_next_candidates(
        parsed=parsed,
        generation_summary=summary,
        tested_hashes=set(),
        max_candidates=8,
        exploration_fraction=0.0,
        family_search_enabled=True,
        family_search_fraction=0.5,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    assert out
    parent_ids = {c.metadata.get("elite_parent") for c in out}
    assert "elite" in parent_ids
    assert "wfc_fail" not in parent_ids


def test_generation_marker_absent_before_validation(tmp_path: Path) -> None:
    gen_dir = tmp_path / "generation_000"
    gen_dir.mkdir(parents=True)
    screening_path = gen_dir / "screening_artifact.json"
    screening_path.write_text(
        json.dumps({"screening_artifact_hash": "abc", "promoted": []}),
        encoding="utf-8",
    )
    summary = {
        "candidates": [{"candidate_id": "c1", "final_status": None}],
    }
    (gen_dir / "generation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    reasons = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening_path,
        summary=summary,
        proposed_candidate_ids=["c1"],
    )
    assert reasons
    assert not (gen_dir / ".generation_complete").is_file()


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


def test_generation_marker_written_after_full_validation(tmp_path: Path) -> None:
    from tests.research_pipeline.test_generation_loop import _fake_filter, _fake_persist

    cfg = AutoresearchConfig(max_generations=1, max_candidates_per_generation=1, run_robustness=False)
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
    gen_dir = generation_dir(tmp_path, report["campaign_id"], 0)
    marker = gen_dir / ".generation_complete"
    summary_path = gen_dir / "generation_summary.json"
    assert marker.is_file()
    assert summary_path.is_file()
    screening = next(gen_dir.glob("pipeline_*/screening_artifact.json"))
    reasons = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening,
        summary=json.loads(summary_path.read_text(encoding="utf-8")),
    )
    assert reasons == []


def test_child_changes_real_feature_recipe_dimension() -> None:
    parsed = _parsed()
    attached = attach_feature_recipe_to_candidate(
        CandidateModel(
            candidate_id="parent",
            model_id="HYP_5",
            strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
            thesis="fade",
        ),
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    base_recipe = dict(attached.feature_recipe or {})
    base_hash = str(attached.feature_recipe_hash)
    summary = {
        "candidates": [
            {
                "elite": True,
                "final_status": FINAL_PASS,
                "candidate_id": "parent",
                "model_id": "HYP_5",
                "strategy_params": dict(attached.strategy_params),
                "feature_recipe": base_recipe,
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
        family_search_enabled=True,
        family_search_fraction=1.0,
        target_event_id="CPI_2024_09_11_TIGHT",
    )
    family_children = [c for c in out if c.metadata.get("refinement") == "family_variant"]
    assert family_children, "expected family-variant child with real recipe dimension change"
    for child in family_children:
        child_recipe = dict(child.feature_recipe or {})
        assert child_recipe != base_recipe
        assert child.feature_recipe_hash != base_hash
        assert compute_feature_recipe_hash(child_recipe) == child.feature_recipe_hash
