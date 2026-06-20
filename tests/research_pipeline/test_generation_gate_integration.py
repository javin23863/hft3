"""Phase 2 integration tests — ontology gate wiring and WF/WFC independence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from research_pipeline.feature_recipe import attach_feature_recipe_to_candidate
from research_pipeline.candidate_manifest import verify_frozen_manifest_integrity
from research_pipeline.generation_gate_chain import (
    FINAL_PASS,
    FINAL_HFT_REJECTED,
    FINAL_REGULAR_WF_REJECTED,
    FINAL_STATISTICAL_REJECTED,
    FINAL_SURFACE_REJECTED,
    FINAL_VECTORBT_REJECTED,
    FINAL_WFC_REJECTED,
    GATE_HFT,
    GATE_MANIFEST,
    GATE_REGULAR_WF,
    GATE_STATISTICAL,
    GATE_SURFACE,
    GATE_VECTORBT,
    GATE_WFC,
    build_gate_receipt,
    run_generation_gate_chain,
)
from research_pipeline.generation_gate_producers import (
    BLOCKED_UNBACKED_AUTHORITY,
    build_hftbacktest_gate_receipt,
    build_manifest_gate_receipt,
    build_statistical_robustness_gate_receipt,
    build_surface_stability_gate_receipt,
    build_vectorbt_gate_receipt,
    build_regular_walk_forward_gate_receipt,
    build_walk_forward_correlation_gate_receipt,
    run_ontology_gate_for_candidate,
)
from research_pipeline.generation_loop import (
    AutoresearchConfig,
    run_single_generation,
)
from research_pipeline.generation_state import default_manifest, generation_dir, save_manifest
from research_pipeline.generation_summary import build_generation_summary
from research_pipeline.hypothesis_parser import parse_hypothesis
from research_pipeline.types import CandidateModel


def _manifest(**overrides: object) -> dict:
    from research_pipeline.candidate_manifest import compute_manifest_hash

    base = {
        "manifest_schema": "candidate_manifest.v1",
        "candidate_id": "cand-001",
        "feature_recipe_hash": "recipe-abc",
        "model_id": "HYP_5",
    }
    base.update(overrides)
    if "manifest_hash" not in overrides:
        base["manifest_hash"] = compute_manifest_hash(base)
    return base


def _valid_hft_replay(
    manifest: dict[str, Any] | None = None,
    *,
    screening_artifact_hash: str = "screen-abc",
    robustness_artifact_hash: str = "rob-abc",
) -> dict[str, Any]:
    m = manifest or _manifest()
    return {
        "candidate_id": m["candidate_id"],
        "manifest_hash": m["manifest_hash"],
        "feature_recipe_hash": m["feature_recipe_hash"],
        "screening_artifact_hash": screening_artifact_hash,
        "robustness_artifact_hash": robustness_artifact_hash,
        "certification_status": "full_fidelity_declared",
    }


def _pass_receipt(gate_id: str) -> dict:
    m = _manifest()
    return build_gate_receipt(
        gate_id=gate_id,
        gate_version="1.0.0",
        candidate_id="cand-001",
        feature_recipe_hash="recipe-abc",
        manifest_hash=str(m["manifest_hash"]),
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
    wf_pass_summary = {"status": "PASS", "periods": [{"name": "Discovery", "gate_pass": True}]}
    wfc_only_summary = {
        "status": "FAIL",
        "wfc_status": "PASS",
        "wfc": {"pearson": 0.8, "spearman": 0.7, "wfc_status": "PASS"},
        "wfc_matrix_rows": [{"parameter_hash": "ph-1", "fold": 0}],
        "periods": [{"name": "Discovery", "gate_pass": False}],
    }
    regular = build_regular_walk_forward_gate_receipt(manifest=manifest, campaign_summary=wf_pass_summary)
    wfc = build_walk_forward_correlation_gate_receipt(manifest=manifest, campaign_summary=wfc_only_summary)
    assert regular["status"] == "PASS"
    assert wfc["status"] == "PASS"
    regular_fail = build_regular_walk_forward_gate_receipt(manifest=manifest, campaign_summary=wfc_only_summary)
    assert regular_fail["status"] == "REJECT"


def test_gate4_rejects_holdout_without_evaluate_only() -> None:
    manifest = _manifest()
    holdout_violation = {
        "status": "PASS",
        "periods": [
            {"name": "Discovery", "gate_pass": True},
            {"name": "Holdout", "gate_pass": True, "evaluate_only": False},
        ],
    }
    receipt = build_regular_walk_forward_gate_receipt(manifest=manifest, campaign_summary=holdout_violation)
    assert receipt["status"] == "REJECT"
    assert any("holdout_evaluate_only_violation" in r for r in receipt.get("failure_reasons") or [])


def test_gate5_rejects_missing_wfc_matrix_alignment() -> None:
    manifest = _manifest()
    no_matrix = {
        "status": "PASS",
        "wfc_status": "PASS",
        "wfc": {"pearson": 0.8, "spearman": 0.7, "wfc_status": "PASS"},
        "wfc_matrix_rows": [],
        "periods": [{"name": "Discovery", "gate_pass": True}],
    }
    receipt = build_walk_forward_correlation_gate_receipt(manifest=manifest, campaign_summary=no_matrix)
    assert receipt["status"] == "REJECT"
    assert "parameter_surface_alignment_missing" in (receipt.get("failure_reasons") or [])


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


def _passing_surface_metrics() -> dict[str, Any]:
    from backtest_pipeline.src.surface_stability import compute_surface_stability

    grid = {
        (r, c): {"net_return": 0.10, "trade_count": 50}
        for r in range(3)
        for c in range(3)
    }
    return compute_surface_stability(grid)


def _passing_vectorbt_row(*, candidate_id: str = "cand-001") -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "screening_status": "pass",
        "vectorbt_results": {
            "oos_expectancy": 1.0,
            "num_trades": 50,
            "hit_rate": 0.55,
        },
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
        "surface_stability_metrics": _passing_surface_metrics(),
    }


def _passing_robustness_input() -> dict[str, Any]:
    from tests.backtest_pipeline.test_robustness_bridge import _full_passing_input

    return _full_passing_input()


def _passing_promoted_row(*, candidate_id: str = "cand-001") -> dict[str, Any]:
    row = _passing_vectorbt_row(candidate_id=candidate_id)
    row["vectorbt_results"]["robustness_input"] = _passing_robustness_input()
    return row


def test_vectorbt_gate_pass_with_official_stats() -> None:
    manifest = _manifest()
    screening = {"screening_artifact_hash": "screen-hash-abc"}
    receipt = build_vectorbt_gate_receipt(
        manifest=manifest,
        promoted_row=_passing_vectorbt_row(),
        screening=screening,
    )
    assert receipt["status"] == "PASS"
    assert receipt["screening_artifact_hash"] == "screen-hash-abc"
    assert receipt["official_stats"]["net_return"] == 0.10


def test_vectorbt_gate_fail_missing_stats() -> None:
    manifest = _manifest()
    row = {"candidate_id": "cand-001", "vectorbt_results": {"oos_expectancy": 1.0}}
    receipt = build_vectorbt_gate_receipt(
        manifest=manifest,
        promoted_row=row,
        screening={"screening_artifact_hash": "hash"},
    )
    assert receipt["status"] == "REJECT"
    assert any("official_stats_missing" in r for r in receipt["failure_reasons"])


def test_surface_gate_pass_with_complete_evidence() -> None:
    manifest = _manifest()
    receipt = build_surface_stability_gate_receipt(
        manifest=manifest,
        promoted_row=_passing_vectorbt_row(),
    )
    assert receipt["status"] == "PASS"
    assert receipt["passed_check_count"] == receipt["required_check_count"]


def test_surface_gate_fail_formula_missing() -> None:
    manifest = _manifest()
    receipt = build_surface_stability_gate_receipt(
        manifest=manifest,
        promoted_row={
            "surface_stability_metrics": {
                "status": "not_run",
                "reason": "surface_stability_formula_authority_missing",
                "formula_authority_status": "missing",
            }
        },
    )
    assert receipt["status"] == "REJECT"


def test_statistical_gate_pass_full_gauntlet() -> None:
    manifest = _manifest()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=manifest,
        promoted_row=_passing_promoted_row(),
        allow_partial=False,
    )
    assert receipt["status"] == "PASS"
    assert receipt["robustness_artifact_staleness"] == "fresh"


def test_partial_statistical_robustness_cannot_pass() -> None:
    manifest = _manifest()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=manifest,
        promoted_row=_passing_vectorbt_row(),
        allow_partial=False,
    )
    assert receipt["status"] in ("REJECT", "NOT_RUN")
    assert receipt["status"] != "PASS"


def test_statistical_gate_fail_dsr() -> None:
    from tests.backtest_pipeline.test_robustness_bridge import _failing_dsr_expectancies

    manifest = _manifest()
    row = _passing_promoted_row()
    row["vectorbt_results"]["robustness_input"]["per_event_expectancies"] = _failing_dsr_expectancies()
    receipt = build_statistical_robustness_gate_receipt(
        manifest=manifest,
        promoted_row=row,
        allow_partial=False,
    )
    assert receipt["status"] == "REJECT"


def test_vectorbt_reject_maps_to_final_vectorbt_rejected() -> None:
    reject = build_vectorbt_gate_receipt(
        manifest=_manifest(),
        promoted_row={"candidate_id": "cand-001", "rejected": True, "vectorbt_results": {}},
        screening={"screening_artifact_hash": "h"},
    )
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=reject,
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_VECTORBT_REJECTED


def test_surface_reject_maps_to_final_surface_rejected() -> None:
    reject = build_surface_stability_gate_receipt(
        manifest=_manifest(),
        promoted_row={"surface_stability_metrics": {"status": "fail"}},
    )
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=reject,
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_SURFACE_REJECTED


def test_statistical_reject_maps_to_final_statistical_rejected() -> None:
    from tests.backtest_pipeline.test_robustness_bridge import _failing_dsr_expectancies

    row = _passing_promoted_row()
    row["vectorbt_results"]["robustness_input"]["per_event_expectancies"] = _failing_dsr_expectancies()
    reject = build_statistical_robustness_gate_receipt(
        manifest=_manifest(),
        promoted_row=row,
        allow_partial=False,
    )
    assert reject["status"] == "REJECT"
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=reject,
        hftbacktest_receipt=None,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_STATISTICAL_REJECTED


def test_manifest_immutability_tamper_rejects() -> None:
    manifest = _manifest()
    errors = verify_frozen_manifest_integrity(manifest)
    assert errors == []
    tampered = dict(manifest)
    tampered["manifest_hash"] = "tampered-hash"
    assert "manifest_hash_immutability_violation" in verify_frozen_manifest_integrity(tampered)
    receipt = build_manifest_gate_receipt(manifest=tampered)
    assert receipt["status"] == "REJECT"


def test_manifest_gate_receipt_emitted_on_generation(tmp_path: Path, monkeypatch) -> None:
    from research_pipeline import generation_loop as gl

    monkeypatch.setattr(
        gl,
        "run_ontology_gate_for_candidate",
        lambda *, manifest, repo_root: build_gate_receipt(
            gate_id="ontology_gate",
            gate_version="1.0.0",
            candidate_id=str(manifest["candidate_id"]),
            feature_recipe_hash=str(manifest["feature_recipe_hash"]),
            manifest_hash=str(manifest.get("manifest_hash") or "pending"),
            status="PASS",
            required_checks=["fable_entry_checklist"],
            required_check_count=1,
            passed_check_count=1,
        ),
    )
    cfg = AutoresearchConfig(max_candidates_per_generation=1, run_robustness=False)
    manifest = default_manifest(
        campaign_id="camp_manifest",
        event_id="E1",
        symbol="MES",
        thesis="fade",
        config_hash="abc",
    )
    manifest["generation_index"] = 0
    save_manifest(tmp_path, manifest)
    cand = _candidate(candidate_id="manifest_cand")
    run_single_generation(
        repo_root=tmp_path,
        manifest=manifest,
        parsed=parse_hypothesis("fade", use_llm=False),
        cfg=cfg,
        candidates=[cand],
        filter_fn=_fake_filter,
        persist_fn=_fake_persist,
    )
    receipt_path = (
        generation_dir(tmp_path, "camp_manifest", 0)
        / "gates"
        / "manifest_cand"
        / "manifest_gate.json"
    )
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["gate_id"] == GATE_MANIFEST


def test_hft_not_run_cannot_elite() -> None:
    chain = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=build_hftbacktest_gate_receipt(
            manifest=_manifest(),
            skipped_reason="hft_campaign_disabled",
        ),
        certification_mode=True,
    )
    summary = build_generation_summary(
        repo_root=Path("."),
        campaign_id="c1",
        generation_index=0,
        screening_artifact={
            "promoted": [
                {
                    "candidate_id": "cand-001",
                    "hypothesis_id": "HYP_5",
                    "vectorbt_results": {"oos_expectancy": 2.0},
                }
            ]
        },
        gate_chain_by_id={"cand-001": chain},
    )
    row = summary["candidates"][0]
    assert row["elite"] is False
    assert summary["best_candidate_id"] is None
    assert row["hft_replay_status"] == "not_run"


def test_final_pass_requires_all_gates_including_hft() -> None:
    manifest = _manifest()
    hft_pass = build_hftbacktest_gate_receipt(
        manifest=manifest,
        scenario_results=[
            {
                "scenario_id": "s1",
                "status": "completed",
                "replay_result": _valid_hft_replay(manifest),
            }
        ],
        screening_artifact_hash="screen-abc",
        robustness_artifact_hash="rob-abc",
        allow_declared_certification=True,
    )
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=hft_pass,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_PASS
    summary = build_generation_summary(
        repo_root=Path("."),
        campaign_id="c1",
        generation_index=0,
        screening_artifact={
            "promoted": [
                {
                    "candidate_id": "cand-001",
                    "hypothesis_id": "HYP_5",
                    "vectorbt_results": {"oos_expectancy": 2.0},
                }
            ]
        },
        gate_chain_by_id={"cand-001": result},
    )
    assert summary["best_candidate_id"] == "cand-001"
    assert summary["candidates"][0]["elite"] is True


def test_hft_reject_maps_to_final_hft_rejected() -> None:
    manifest = _manifest()
    reject = build_hftbacktest_gate_receipt(
        manifest=manifest,
        scenario_results=[
            {
                "scenario_id": "s1",
                "status": "failed",
                "replay_result": {"error": "boom", **_valid_hft_replay(manifest)},
            }
        ],
        screening_artifact_hash="screen-abc",
        robustness_artifact_hash="rob-abc",
    )
    assert reject["status"] == "REJECT"
    result = run_generation_gate_chain(
        candidate_manifest=_manifest(),
        ontology_receipt=_pass_receipt("ontology_gate"),
        vectorbt_receipt=_pass_receipt(GATE_VECTORBT),
        surface_receipt=_pass_receipt(GATE_SURFACE),
        regular_walk_forward_receipt=_pass_receipt(GATE_REGULAR_WF),
        walk_forward_correlation_receipt=_pass_receipt(GATE_WFC),
        statistical_receipt=_pass_receipt(GATE_STATISTICAL),
        hftbacktest_receipt=reject,
        certification_mode=True,
    )
    assert result["final_status"] == FINAL_HFT_REJECTED
    assert result["stopped_at_gate"] == GATE_HFT
