"""Phase 2 integration tests — ontology gate wiring and WF/WFC independence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate
from research_pipeline.generation_gate_chain import (
    FINAL_REGULAR_WF_REJECTED,
    FINAL_WFC_REJECTED,
    GATE_REGULAR_WF,
    GATE_WFC,
    build_gate_receipt,
    run_generation_gate_chain,
)
from research_pipeline.generation_gate_producers import (
    BLOCKED_UNBACKED_AUTHORITY,
    build_regular_walk_forward_gate_receipt,
    build_walk_forward_correlation_gate_receipt,
    run_ontology_gate_for_candidate,
)
from research_pipeline.generation_loop import (
    AutoresearchConfig,
    run_single_generation,
)
from research_pipeline.generation_state import default_manifest, generation_dir, save_manifest
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.types import CandidateModel


def _manifest(**overrides: object) -> dict:
    base = {
        "manifest_schema": "candidate_manifest.v1",
        "candidate_id": "cand-001",
        "feature_recipe_hash": "recipe-abc",
        "manifest_hash": "manifest-xyz",
        "model_id": "HYP_5",
    }
    base.update(overrides)
    return base


def _pass_receipt(gate_id: str) -> dict:
    return build_gate_receipt(
        gate_id=gate_id,
        gate_version="1.0.0",
        candidate_id="cand-001",
        feature_recipe_hash="recipe-abc",
        manifest_hash="manifest-xyz",
        status="PASS",
        required_checks=["check_a", "check_b"],
        required_check_count=2,
        passed_check_count=2,
        failed_check_count=0,
        missing_check_count=0,
        authority_refs=["docs/project/ROBUSTNESS_TESTING_SPEC.md"],
    )


def _candidate(*, candidate_id: str = "c1", ontology_citations: list[dict] | None = None) -> CandidateModel:
    parsed = parse_hypothesis("fade blowout on CPI", use_llm=False)
    cand = CandidateModel(
        candidate_id=candidate_id,
        model_id="HYP_5",
        strategy_params={"signal_threshold": 0.15, "holding_period_bars": 15},
        thesis="fade",
        metadata={"ontology_citations": ontology_citations} if ontology_citations else {},
    )
    return attach_feature_recipe_to_candidate(
        cand,
        parsed=parsed,
        target_event_id="CPI_2024_09_11_TIGHT",
        target_symbol="MES",
    )


class _FakeFilterResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _fake_filter(*, candidates, **kwargs):
    promoted = [
        {
            "candidate_id": c.candidate_id,
            "hypothesis_id": c.model_id,
            "param_values": dict(c.strategy_params),
            "vectorbt_results": {"oos_expectancy": 1.0, "max_drawdown_pct": -5.0},
        }
        for c in candidates
    ]
    return _FakeFilterResult(
        {
            "screening_backend": "vectorbt",
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


def test_unbacked_ontology_gate_blocked_status() -> None:
    manifest = {
        "candidate_id": "c-unbacked",
        "feature_recipe_hash": "hash1",
        "manifest_hash": "mhash1",
        "ontology_citations": [
            {"paper_id": "nonexistent-paper-2099", "spec_ref": "NONEXISTENT_SPEC.md"}
        ],
    }
    receipt = run_ontology_gate_for_candidate(manifest=manifest, repo_root=Path("."))
    assert receipt["status"] == "BLOCKED"
    assert any(BLOCKED_UNBACKED_AUTHORITY in str(r) for r in receipt.get("failure_reasons") or [])


def test_unbacked_candidate_cannot_consume_vectorbt(tmp_path: Path, monkeypatch) -> None:
    from research_pipeline.generation_gate_chain import GATE_ONTOLOGY, build_gate_receipt
    from research_pipeline import generation_loop as gl

    real_run = run_ontology_gate_for_candidate

    def _ontology(*, manifest, repo_root):
        if str(manifest["candidate_id"]) == "unbacked":
            return real_run(manifest=manifest, repo_root=repo_root)
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

    monkeypatch.setattr(gl, "run_ontology_gate_for_candidate", _ontology)

    filter_calls: list[str] = []

    def filter_fn(*, candidates, **kwargs):
        filter_calls.extend([c.candidate_id for c in candidates])
        return _fake_filter(candidates=candidates, **kwargs)

    backed = _candidate(candidate_id="backed")
    unbacked = _candidate(
        candidate_id="unbacked",
        ontology_citations=[{"paper_id": "nonexistent-paper-2099", "spec_ref": "FAKE.md"}],
    )
    cfg = AutoresearchConfig(max_candidates_per_generation=2, run_robustness=False)
    manifest = default_manifest(
        campaign_id="camp_ontology",
        event_id="E1",
        symbol="MES",
        thesis="fade",
        config_hash="abc",
    )
    manifest["generation_index"] = 0
    save_manifest(tmp_path, manifest)

    run_single_generation(
        repo_root=tmp_path,
        manifest=manifest,
        parsed=parse_hypothesis("fade", use_llm=False),
        cfg=cfg,
        candidates=[backed, unbacked],
        filter_fn=filter_fn,
        persist_fn=_fake_persist,
    )
    assert "unbacked" not in filter_calls
    assert "backed" in filter_calls
    unbacked_receipt = json.loads(
        (
            generation_dir(tmp_path, "camp_ontology", 0)
            / "gates"
            / "unbacked"
            / "ontology_gate.json"
        ).read_text(encoding="utf-8")
    )
    assert unbacked_receipt["status"] == "BLOCKED"


def test_regular_wf_pass_cannot_substitute_wfc() -> None:
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt("vectorbt_gate"),
        surface_receipt=_pass_receipt("surface_stability_gate"),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=None,
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] != "FINAL_PASS"
    assert result["stopped_at_gate"] == GATE_WFC


def test_wfc_pass_cannot_substitute_regular_wf() -> None:
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt("vectorbt_gate"),
        surface_receipt=_pass_receipt("surface_stability_gate"),
        regular_walk_forward_receipt=None,
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] != "FINAL_PASS"
    assert result["stopped_at_gate"] == GATE_REGULAR_WF


def test_wf_receipts_are_independent_producers() -> None:
    manifest = _manifest()
    wf_pass_summary = {"status": "PASS", "periods": [{"gate_pass": True}]}
    wfc_only_summary = {
        "status": "FAIL",
        "wfc_status": "PASS",
        "wfc": {"pearson": 0.8, "spearman": 0.7, "wfc_status": "PASS"},
        "periods": [{"gate_pass": False}],
    }
    regular = build_regular_walk_forward_gate_receipt(manifest=manifest, campaign_summary=wf_pass_summary)
    wfc = build_walk_forward_correlation_gate_receipt(manifest=manifest, campaign_summary=wfc_only_summary)
    assert regular["status"] == "PASS"
    assert wfc["status"] == "PASS"
    regular_fail = build_regular_walk_forward_gate_receipt(manifest=manifest, campaign_summary=wfc_only_summary)
    assert regular_fail["status"] == "REJECT"


def test_wfc_reject_maps_to_final_wfc_rejected() -> None:
    reject = _pass_receipt(GATE_WFC)
    reject["status"] = "REJECT"
    reject["passed_check_count"] = 0
    reject["failed_check_count"] = 1
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt("vectorbt_gate"),
        surface_receipt=_pass_receipt("surface_stability_gate"),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=reject,
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_WFC_REJECTED


def test_regular_wf_reject_maps_to_final_regular_wf_rejected() -> None:
    reject = _pass_receipt(GATE_REGULAR_WF)
    reject["status"] = "REJECT"
    reject["passed_check_count"] = 0
    reject["failed_check_count"] = 1
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt("vectorbt_gate"),
        surface_receipt=_pass_receipt("surface_stability_gate"),
        regular_walk_forward_receipt=reject,
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=None,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_REGULAR_WF_REJECTED
