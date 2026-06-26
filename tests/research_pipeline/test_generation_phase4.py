"""Phase 4 tests: honest completion + deterministic resume."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from research_pipeline.generation_gate_chain import FINAL_PASS, build_gate_receipt, GATE_ONTOLOGY
from research_pipeline.generation_gate_producers import gate_receipt_path, write_gate_receipt
from research_pipeline.generation_loop import (
    AutoresearchConfig,
    _campaign_config_hash,
    run_autoresearch_loop,
    run_single_generation,
)
from research_pipeline.generation_state import (
    compute_config_hash,
    default_manifest,
    generation_dir,
    load_manifest,
    save_manifest,
)
from research_pipeline.generation_summary import (
    validate_generation_artifacts,
    validate_generation_completion,
)
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.types import CandidateModel, ParsedHypothesis
from tests.research_pipeline.test_generation_loop import _fake_filter, _fake_persist


@pytest.fixture(autouse=True)
def _pass_ontology_gate(monkeypatch):
    from research_pipeline.generation_gate_chain import build_gate_receipt as bgr
    from research_pipeline import generation_loop as gl

    def _pass_ontology(*, manifest, repo_root):
        return bgr(
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


def _parsed() -> ParsedHypothesis:
    return parse_hypothesis("fade blowout", use_llm=False)


def _candidate(cid: str = "cand_a") -> CandidateModel:
    return CandidateModel(
        candidate_id=cid,
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
        thesis="fade blowout",
    )


def test_completion_marker_written_only_after_validation(tmp_path: Path) -> None:
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
    artifact_reasons = validate_generation_artifacts(
        gen_dir=gen_dir,
        screening_path=screening,
        summary=json.loads(summary_path.read_text(encoding="utf-8")),
    )
    assert artifact_reasons == []


def test_missing_evidence_blocks_complete(tmp_path: Path) -> None:
    gen_dir = tmp_path / "generation_000"
    gen_dir.mkdir(parents=True)
    screening_path = gen_dir / "screening_artifact.json"
    screening_path.write_text(json.dumps({"screening_artifact_hash": "abc", "promoted": []}), encoding="utf-8")
    summary = {
        "candidates": [
            {
                "candidate_id": "c1",
                "final_status": None,
            }
        ]
    }
    (gen_dir / "generation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    reasons = validate_generation_completion(
        gen_dir=gen_dir,
        screening_path=screening_path,
        summary=summary,
        proposed_candidate_ids=["c1"],
    )
    assert any("non_terminal" in r for r in reasons)
    assert not (gen_dir / ".generation_complete").is_file()


def test_changed_config_blocks_resume(tmp_path: Path) -> None:
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
    campaign_id = report["campaign_id"]
    cfg2 = AutoresearchConfig(
        max_generations=2,
        max_candidates_per_generation=1,
        run_robustness=False,
        screening_scope="full",
    )
    with pytest.raises(ValueError, match="config_hash mismatch"):
        run_autoresearch_loop(
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


def test_config_hash_includes_wfc_and_gate_versions(tmp_path: Path) -> None:
    wf = tmp_path / "apps" / "workbench" / "config"
    wf.mkdir(parents=True)
    (wf / "walk_forward.yaml").write_text("periods:\n  - name: Discovery\n", encoding="utf-8")
    (wf / "wfc_gate.yaml").write_text("pearson_min: 0.20\n", encoding="utf-8")
    cfg = AutoresearchConfig(max_generations=1, screening_scope="pilot")
    h1 = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg)
    (wf / "wfc_gate.yaml").write_text("pearson_min: 0.50\n", encoding="utf-8")
    h2 = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg)
    assert h1 != h2


def test_corrupt_receipt_rerun_on_resume(tmp_path: Path, monkeypatch) -> None:
    ontology_calls: list[str] = []

    def counting_ontology(*, manifest, repo_root):
        ontology_calls.append(str(manifest["candidate_id"]))
        from research_pipeline.generation_gate_chain import build_gate_receipt as bgr

        return bgr(
            gate_id=GATE_ONTOLOGY,
            gate_version="1.0.0",
            candidate_id=str(manifest["candidate_id"]),
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest.get("manifest_hash") or "pending"),
            status="PASS",
            required_checks=["fable_entry_checklist"],
            required_check_count=1,
            passed_check_count=1,
        )

    from research_pipeline import generation_loop as gl

    monkeypatch.setattr(gl, "run_ontology_gate_for_candidate", counting_ontology)

    cfg = AutoresearchConfig(max_generations=1, max_candidates_per_generation=1, run_robustness=False)
    manifest = default_manifest(
        campaign_id="camp_resume",
        event_id="E1",
        symbol="MES",
        thesis="fade",
        config_hash=_campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg),
    )
    save_manifest(tmp_path, manifest)
    gen_dir = generation_dir(tmp_path, "camp_resume", 0)
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "generation_checkpoint.json").write_text(
        json.dumps({"proposed_candidate_ids": ["cand_a"], "pipeline_run_id": "pipeline_test", "generation_index": 0}),
        encoding="utf-8",
    )
    (gen_dir / "proposed_candidates.json").write_text(
        json.dumps(
            [
                {
                    "candidate_id": "cand_a",
                    "model_id": "HYP_5",
                    "strategy_params": {"signal_threshold": 0.15},
                    "thesis": "fade",
                    "metadata": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    write_gate_receipt(
        gate_receipt_path(gen_dir, "cand_a", "ontology_gate"),
        {"invalid": "receipt"},
    )

    run_single_generation(
        repo_root=tmp_path,
        manifest=manifest,
        parsed=_parsed(),
        cfg=cfg,
        candidates=[_candidate()],
        resume=True,
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    assert ontology_calls == ["cand_a"]
    receipt, errors = __import__(
        "research_pipeline.generation_summary", fromlist=["load_gate_receipt"]
    ).load_gate_receipt(gate_receipt_path(gen_dir, "cand_a", "ontology_gate"))
    assert not errors
    assert receipt is not None


def test_zero_final_pass_can_still_complete(tmp_path: Path, monkeypatch) -> None:
    def reject_filter(**kwargs):
        return _fake_filter(**kwargs).__class__(
            {
                **_fake_filter(**kwargs).to_dict(),
                "promoted": [],
                "promoted_ids": [],
            }
        )

    cfg = AutoresearchConfig(max_generations=1, max_candidates_per_generation=1, run_robustness=False)
    code, report = run_autoresearch_loop(
        repo_root=tmp_path,
        thesis="fade",
        event_id="E1",
        cfg=cfg,
        no_llm=True,
        filter_fn=reject_filter,
        persist_fn=_fake_persist,
    )
    assert code == 0
    gen_dir = generation_dir(tmp_path, report["campaign_id"], 0)
    summary = json.loads((gen_dir / "generation_summary.json").read_text(encoding="utf-8"))
    assert summary.get("final_pass_count") == 0
    assert (gen_dir / ".generation_complete").is_file()

def test_config_hash_includes_skip_bad_units(tmp_path: Path) -> None:
    cfg_a = AutoresearchConfig(max_generations=1, skipped_unit_ids=("unit_a",))
    cfg_b = AutoresearchConfig(max_generations=1, skipped_unit_ids=("unit_b",))
    h_a = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg_a)
    h_b = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg_b)
    assert h_a != h_b


def test_config_hash_includes_skip_bad_units_file_content(tmp_path: Path) -> None:
    skip_a = tmp_path / "skip_a.json"
    skip_b = tmp_path / "skip_b.json"
    skip_a.write_text('{"skipped_unit_ids": ["unit_x"]}', encoding="utf-8")
    skip_b.write_text('{"skipped_unit_ids": ["unit_y"]}', encoding="utf-8")
    cfg_a = AutoresearchConfig(max_generations=1, skip_bad_units_file=skip_a)
    cfg_b = AutoresearchConfig(max_generations=1, skip_bad_units_file=skip_b)
    h_a = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg_a)
    h_b = _campaign_config_hash(repo_root=tmp_path, event_id="E1", cfg=cfg_b)
    assert h_a != h_b

