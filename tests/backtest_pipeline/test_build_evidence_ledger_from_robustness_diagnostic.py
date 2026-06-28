from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backtest_pipeline.src.vectorbt_adapter import (
    compute_screening_artifact_hash,
    validate_screening_artifact,
)
from scripts.apply_robustness_evidence_to_screening import apply_robustness_evidence
from scripts.build_robustness_raw_inputs_from_screening import _load_screening_evidence
from test_apply_robustness_evidence_to_screening import _passing_evidence, _screening_artifact
from test_build_robustness_raw_inputs_from_screening import (
    _complete_surface_artifact,
    _first_event_fail_artifact,
    _write_event_unit_artifacts,
)
from scripts.build_evidence_ledger_from_robustness_diagnostic import _classify_family, _number

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_evidence_ledger_from_robustness_diagnostic.py"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rewrite_artifact_family(
    artifact: dict[str, Any],
    *,
    prefix: str,
    model_id: str,
    symbol: str,
    research_clock: str = "scheduled_event",
) -> dict[str, Any]:
    artifact = copy.deepcopy(artifact)
    reason_by_old_id = dict(artifact.get("candidate_reasons", {}))
    for row in [*artifact["promoted"], *artifact["rejected"]]:
        old_id = str(row["candidate_id"])
        new_id = f"{prefix}_{old_id}"
        row["candidate_id"] = new_id
        row["model_id"] = model_id
        row["hypothesis_id"] = model_id
        row["symbol"] = symbol
        metadata = row.get("base_candidate_metadata")
        assert isinstance(metadata, dict)
        metadata["symbol"] = symbol
        metadata["event_type"] = "CPI"
        metadata["research_clock"] = research_clock
        metadata["context_set_id"] = "target_only"
        metadata["allowed_context_set_id"] = "target_only"
        row["base_candidate_id"] = f"{model_id}|{symbol}.v.0|{metadata['event_id']}|10"
        row["feature_recipe_hash"] = f"recipe_{prefix}_{metadata['event_id']}"
        metadata["feature_recipe_hash"] = row["feature_recipe_hash"]
        metrics = row.get("metric_values")
        if isinstance(metrics, dict):
            metrics["base_candidate_id"] = row["base_candidate_id"]
            metrics["base_candidate_metadata"] = metadata
            metrics["feature_recipe_hash"] = row["feature_recipe_hash"]
        reason = reason_by_old_id.get(old_id, row.get("rejection_reason_or_null") or "pass")
        row["_test_reason"] = reason

    artifact["promoted_ids"] = [row["candidate_id"] for row in artifact["promoted"]]
    artifact["rejected_ids"] = [row["candidate_id"] for row in artifact["rejected"]]
    artifact["candidate_ids"] = artifact["promoted_ids"] + artifact["rejected_ids"]
    artifact["promoted_reasons"] = {
        row["candidate_id"]: str(row.pop("_test_reason", "pass")) for row in artifact["promoted"]
    }
    artifact["rejected_reasons"] = {
        row["candidate_id"]: str(row.pop("_test_reason", "promotion_gate_failed"))
        for row in artifact["rejected"]
    }
    artifact["candidate_reasons"] = {
        **artifact["promoted_reasons"],
        **artifact["rejected_reasons"],
    }
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)
    return artifact


def _family_map(model_id: str, symbol: str, research_clock: str = "scheduled_event") -> dict[str, str]:
    return {
        "model_id": model_id,
        "symbol": symbol,
        "event_type": "CPI",
        "research_clock": research_clock,
        "context_set_id": "target_only",
    }


def _eligible_unit_artifact(tmp_path: Path, candidate_id: str = "eligible_fixture") -> dict[str, Any]:
    source = _screening_artifact(candidate_id)
    row = source["promoted"][0]
    row["base_candidate_id"] = f"HYP_5|MES.v.0|CPI_2020_01_14_TIGHT|10"
    row["base_candidate_metadata"] = {
        "event_id": "CPI_2020_01_14_TIGHT",
        "target_event_id": "CPI_2020_01_14_TIGHT",
        "event_type": "CPI",
        "symbol": "MES",
        "research_clock": "event_window_pilot",
        "context_set_id": "target_only",
        "allowed_context_set_id": "target_only",
        "feature_recipe_hash": row["feature_recipe_hash"],
    }
    source["screening_artifact_hash"] = compute_screening_artifact_hash(source)
    validate_screening_artifact(source)
    screening_path = tmp_path / "eligible_source_screening_artifact.json"
    evidence_path = tmp_path / "eligible_robustness_evidence.json"
    out_path = tmp_path / "eligible_screening_artifact.json"
    _write_json(screening_path, source)
    _write_json(evidence_path, _passing_evidence(source, candidate_id))
    apply_robustness_evidence(
        screening_artifact_path=screening_path,
        robustness_evidence_path=evidence_path,
        out_path=out_path,
        candidate_ids=None,
        min_eligible=1,
    )
    artifact = json.loads(out_path.read_text(encoding="utf-8"))
    validate_screening_artifact(artifact)
    return artifact


def _family_report(
    *,
    model_id: str,
    symbol: str,
    packaging_eligible: bool,
    current_pass: bool,
    reason: str = "",
    research_clock: str = "scheduled_event",
    parameter_cells: int = 16,
    missing_cells: int = 0,
    insufficient_trade_cells: int = 0,
) -> dict[str, Any]:
    rejected_events = []
    if missing_cells or insufficient_trade_cells:
        reasons = []
        if missing_cells:
            reasons.append("missing_surface")
        if insufficient_trade_cells:
            reasons.append("insufficient_trades")
        rejected_events.append(
            {
                "event_id": "CPI_2020_04_10_TIGHT",
                "event_date": "2020-04-10",
                "reasons": reasons,
                "missing_parameter_cell_count": missing_cells,
                "insufficient_trade_cell_count": insufficient_trade_cells,
            }
        )
    return {
        "model_family": _family_map(model_id, symbol, research_clock),
        "vectorbt_promoted_count": 1,
        "packaging_eligible": packaging_eligible,
        "packaging_failure_reason": "" if packaging_eligible else reason,
        "event_count": 4,
        "usable_event_count": 4,
        "rejected_event_count": len(rejected_events),
        "rejected_events": rejected_events,
        "surface_training_event_count": 3,
        "surface_training_event_ids": [
            "CPI_2020_01_14_TIGHT",
            "CPI_2020_02_13_TIGHT",
            "CPI_2020_03_11_TIGHT",
        ],
        "parameter_cell_count": parameter_cells,
        "complete_parameter_combination_count": 4 if packaging_eligible else 3,
        "event_0_id": "CPI_2020_01_14_TIGHT",
        "event_0_surface_metrics": {
            "status": "pass" if current_pass else "fail",
            "plateau_score": 0.81 if current_pass else 0.2,
            "reason": "" if current_pass else "surface_stability_metrics_not_replay_ready",
        },
        "pooled_surface_metrics": {"status": "fail", "median_plateau_score": 0.4},
        "median_event_surface_metrics": {"status": "fail", "median_plateau_score": 0.4},
        "fold_is_surface_metrics": {"status": "fail", "surface_count": 3, "surface_pass_count": 1},
        "surface_policy_passes": {"current_first_event": current_pass},
        "policy_failure_reasons": {
            "current_first_event": "" if current_pass else "surface_stability_metrics_not_replay_ready"
        },
        "current_first_event_pass": current_pass,
        "pooled_train_events_pass": False,
        "median_event_surface_pass": False,
        "fold_is_surface_pass": False,
        "candidates_passing_current_first_event": 1 if current_pass else 0,
        "candidate_ids_passing_current_first_event": [],
        "candidates_rejected_by_current_but_passed_by_corrected_policy": [],
    }


def _write_sensitivity_report(path: Path, unit_dir: Path, tmp_path: Path) -> dict[str, Any]:
    evidence = _load_screening_evidence(
        screening_artifact_path=None,
        screening_artifact_dir=unit_dir,
        source_root=tmp_path,
    )
    report = {
        "schema": "hft3_robustness_bridge_sensitivity_report_v1",
        "screening_artifact": str(unit_dir),
        "screening_artifact_hash": evidence.artifact["screening_artifact_hash"],
        "screening_artifact_source": "unit_artifact_directory",
        "unit_artifact_count": evidence.artifact["unit_artifact_count"],
        "unit_artifact_set_hash": evidence.artifact["unit_artifact_set_hash"],
        "selected_surface_policy": "current_first_event",
        "baseline_surface_policy": "current_first_event",
        "summary": {
            "vectorbt_promoted_count": evidence.promoted_count,
            "model_family_count": 4,
            "packaging_eligible_family_count": 3,
            "packaged_count": 0,
            "min_packaged": 1,
            "hftbacktest_eligible_candidates": 0,
        },
        "families": [
            _family_report(
                model_id="ROBUST_FAIL_MODEL",
                symbol="ES",
                packaging_eligible=True,
                current_pass=False,
            ),
            _family_report(
                model_id="INCOMPLETE_MODEL",
                symbol="NQ",
                packaging_eligible=False,
                current_pass=False,
                reason="incomplete_event_parameter_surface:0.937500<1.000000",
                parameter_cells=15,
                missing_cells=1,
                insufficient_trade_cells=1,
            ),
            _family_report(
                model_id="HYP_5",
                symbol="MES",
                packaging_eligible=True,
                current_pass=True,
                research_clock="event_window_pilot",
            ),
            _family_report(
                model_id="ROBUST_PASS_NEEDS_APPLY",
                symbol="YM",
                packaging_eligible=True,
                current_pass=True,
            ),
        ],
        "assembler_diagnostics": {
            "row_skip_counts": {},
            "family_skip_counts": {},
            "candidate_skip_counts": {},
        },
        "attrition": {},
    }
    _write_json(path, report)
    return report


def test_non_packaging_classification_preserves_adapter_then_data_then_surface() -> None:
    adapter_report = _family_report(
        model_id="ADAPTER_MODEL",
        symbol="ES",
        packaging_eligible=False,
        current_pass=False,
        reason="schema_contract_failure",
        insufficient_trade_cells=1,
    )
    data_report = _family_report(
        model_id="DATA_MODEL",
        symbol="NQ",
        packaging_eligible=False,
        current_pass=False,
        reason="incomplete_event_parameter_surface:0.937500<1.000000",
        missing_cells=1,
        insufficient_trade_cells=1,
    )
    surface_report = _family_report(
        model_id="SURFACE_MODEL",
        symbol="RTY",
        packaging_eligible=False,
        current_pass=False,
        reason="incomplete_event_parameter_surface:0.937500<1.000000",
        missing_cells=1,
    )

    classify_kwargs = {
        "selected_surface_policy": "current_first_event",
        "has_hftbacktest_eligible_candidate": False,
    }
    assert _classify_family(adapter_report, **classify_kwargs)[0] == "adapter_contract_failure"
    assert _classify_family(data_report, **classify_kwargs)[0] == "data_quality_failure"
    assert _classify_family(surface_report, **classify_kwargs)[0] == "surface_incomplete_missing_cells"


def test_builds_diagnostic_ledger_and_classifies_families(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    robust_fail = _rewrite_artifact_family(
        _first_event_fail_artifact(),
        prefix="fail",
        model_id="ROBUST_FAIL_MODEL",
        symbol="ES",
    )
    incomplete = _rewrite_artifact_family(
        _complete_surface_artifact(omit_last_cell=True),
        prefix="incomplete",
        model_id="INCOMPLETE_MODEL",
        symbol="NQ",
    )
    _write_event_unit_artifacts(unit_dir / "robust_fail", robust_fail)
    _write_event_unit_artifacts(unit_dir / "incomplete", incomplete)
    _write_json(unit_dir / "eligible" / "screening_artifact.json", _eligible_unit_artifact(tmp_path))
    mixed_eligible_sibling = _rewrite_artifact_family(
        _complete_surface_artifact(),
        prefix="eligible_sibling",
        model_id="HYP_5",
        symbol="MES",
        research_clock="event_window_pilot",
    )
    _write_event_unit_artifacts(unit_dir / "eligible_sibling", mixed_eligible_sibling)
    robust_pass_needs_apply = _rewrite_artifact_family(
        _complete_surface_artifact(),
        prefix="needs_apply",
        model_id="ROBUST_PASS_NEEDS_APPLY",
        symbol="YM",
    )
    _write_event_unit_artifacts(unit_dir / "needs_apply", robust_pass_needs_apply)
    report_path = tmp_path / "sensitivity.json"
    _write_sensitivity_report(report_path, unit_dir, tmp_path)

    out_dir = tmp_path / "ledger"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sensitivity-report",
            str(report_path),
            "--screening-artifact-dir",
            str(unit_dir),
            "--out-dir",
            str(out_dir),
            "--run-id",
            "ledger_test",
            "--source-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for filename in (
        "candidate_evidence.jsonl",
        "family_readiness.jsonl",
        "gate_summary.json",
        "robustness_bridge_readiness_report.md",
    ):
        assert (out_dir / filename).exists()

    family_rows = [
        json.loads(line) for line in (out_dir / "family_readiness.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    families_by_model = {row["model_family"]["model_id"]: row for row in family_rows}
    assert families_by_model["ROBUST_FAIL_MODEL"]["classification_bucket"] == (
        "robustness_fail_complete_evidence"
    )
    robust_refs = families_by_model["ROBUST_FAIL_MODEL"]["diagnostic_artifact_refs"]
    for ref in (
        robust_refs["family_surface_matrix_jsonl"],
        robust_refs["family_surface_coverage_json"],
        robust_refs["family_gate_decision_json"],
        robust_refs["fold_persistence_matrix_json"],
        robust_refs["fold_gate_decision_json"],
    ):
        assert (tmp_path / ref).exists()
    assert families_by_model["INCOMPLETE_MODEL"]["classification_bucket"] == "data_quality_failure"
    assert families_by_model["INCOMPLETE_MODEL"]["surface_completeness_ratio"] == 0.9375
    assert families_by_model["HYP_5"]["classification_bucket"] == (
        "hftbacktest_eligible_derived"
    )
    assert families_by_model["ROBUST_PASS_NEEDS_APPLY"]["classification_bucket"] == (
        "robustness_pass_needs_evidence_apply"
    )
    assert families_by_model["ROBUST_PASS_NEEDS_APPLY"]["packaging_gate_status"] == "pass"
    assert families_by_model["ROBUST_PASS_NEEDS_APPLY"]["robustness_gate_status"] == "pass"
    assert families_by_model["ROBUST_PASS_NEEDS_APPLY"]["recommended_next_action"].startswith(
        "Run the explicit robustness evidence applicator"
    )

    candidate_rows = [
        json.loads(line) for line in (out_dir / "candidate_evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    eligible = next(row for row in candidate_rows if row["candidate_id"] == "eligible_fixture")
    assert eligible["replay_eligibility_status"] == "eligible"
    assert eligible["robustness_evidence_receipt_status"] == "present"
    assert eligible["validator_reasons"] == []
    assert eligible["hftbacktest_eligible_derived"] is True
    assert eligible["family_classification_bucket"] == "hftbacktest_eligible_derived"
    sibling = next(
        row
        for row in candidate_rows
        if str(row["candidate_id"]).startswith("eligible_sibling_prom_")
    )
    assert sibling["hftbacktest_eligible_derived"] is False
    assert sibling["family_classification_bucket"] == "robustness_pass_needs_evidence_apply"

    summary = json.loads((out_dir / "gate_summary.json").read_text(encoding="utf-8"))
    assert summary["any_hftbacktest_eligible_derived"] is True
    assert summary["families_by_bucket"]["robustness_fail_complete_evidence"] == 1
    assert summary["families_by_bucket"]["robustness_pass_needs_evidence_apply"] == 1
    assert summary["families_by_bucket"]["surface_incomplete_missing_cells"] == 0
    assert summary["families_by_bucket"]["data_quality_failure"] == 1
    assert summary["families_by_bucket"]["hftbacktest_eligible_derived"] == 1

    report = (out_dir / "robustness_bridge_readiness_report.md").read_text(encoding="utf-8")
    assert "## Seven Questions" in report
    assert "Any HftBacktest-derived eligible candidates: true" in report


def test_number_rejects_non_finite_values() -> None:
    assert _number(float("inf")) is None
    assert _number("-inf") is None
    assert _number("nan") is None
    assert _number("1.25") == 1.25


def test_sensitivity_report_binding_fails_closed_when_hash_missing(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units"
    artifact = _rewrite_artifact_family(
        _complete_surface_artifact(),
        prefix="fail",
        model_id="ROBUST_FAIL_MODEL",
        symbol="ES",
    )
    _write_event_unit_artifacts(unit_dir, artifact)
    report_path = tmp_path / "sensitivity.json"
    report = _write_sensitivity_report(report_path, unit_dir, tmp_path)
    report.pop("screening_artifact_hash")
    _write_json(report_path, report)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sensitivity-report",
            str(report_path),
            "--screening-artifact-dir",
            str(unit_dir),
            "--out-dir",
            str(tmp_path / "ledger"),
            "--source-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "sensitivity_report_screening_artifact_hash_missing" in result.stderr
    assert not (tmp_path / "ledger" / "gate_summary.json").exists()
