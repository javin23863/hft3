from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backtest_pipeline.src.hftbacktest_realism import validate_candidate_replay_eligibility
from backtest_pipeline.src.feature_plane import build_feature_plane_payload
from backtest_pipeline.src.vectorbt_adapter import (
    SURFACE_STABILITY_FORMULA_AUTHORITY_MISSING_REASON,
    SURFACE_STABILITY_FORMULA_AUTHORITY_POINTER,
    SURFACE_STABILITY_REQUIRED_CHECKS,
    compute_screening_artifact_hash,
    validate_screening_artifact,
    _parameter_values_hash,
)
from hft_screening_fixtures import replay_eligible_promoted_candidate
from test_robustness_bridge import _full_passing_input

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "apply_robustness_evidence_to_screening.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "apply_robustness_evidence_to_screening_test_module",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _surface_missing() -> dict[str, Any]:
    return {
        "status": "not_run",
        "reason": SURFACE_STABILITY_FORMULA_AUTHORITY_MISSING_REASON,
        "authority": SURFACE_STABILITY_FORMULA_AUTHORITY_POINTER,
        "formula_authority_status": "missing",
        "literature_or_ontology_citation": "docs/project/ROBUSTNESS_TESTING_SPEC.md:130-144",
        "required_checks": list(SURFACE_STABILITY_REQUIRED_CHECKS),
        "failure_semantics": "SURFACE_STABILITY_FORMULA_MISSING",
    }


def _surface_pass() -> dict[str, Any]:
    return {
        "status": "pass",
        "formula_authority_status": "defined",
        "required_checks": list(SURFACE_STABILITY_REQUIRED_CHECKS),
        "plateau_score": 0.82,
        "plateau_width": 3,
        "neighbor_stability": 0.91,
        "cliff_distance_from_loss_regions": 2,
        "parameter_perturbation_sensitivity": 0.08,
        "peak_vs_plateau_comparison": 0.96,
        "minimum_sample_size": 120,
    }


def _not_run(reason: str = "test_explicit_evidence_not_applied") -> dict[str, str]:
    return {"status": "not_run", "reason": reason}


def _pilot_row(candidate_id: str) -> dict[str, Any]:
    row = replay_eligible_promoted_candidate(candidate_id)
    row["parameter_values_hash"] = _parameter_values_hash(row["parameter_values"])
    row["feature_recipe_hash"] = "sha256:feature_recipe_apply_test"
    row["surface_stability_metrics"] = _surface_missing()
    row["robustness_gate_scope"] = "pilot"
    row["wfc_status"] = "not_run"
    row["dsr_status"] = "not_run"
    row["pbo_status"] = "not_run"
    row["cscv_status"] = "not_run"
    row["robustness_artifact_staleness"] = "stale"
    row["walk_forward_metrics"] = _not_run()
    row["wfc_metrics"] = _not_run()
    for field_name in (
        "bootstrap_ci_or_not_run",
        "dsr_or_not_run",
        "pbo_or_not_run",
        "cscv_count_or_not_run",
        "fee_stress_or_not_run",
        "slippage_stress_or_not_run",
        "latency_stress_or_not_run",
        "holm_bh_or_not_run",
        "null_battery_or_not_run",
        "planted_alpha_or_not_run",
        "adversarial_or_not_run",
        "parameter_perturbation_or_not_run",
    ):
        row[field_name] = _not_run()
    row["replay_eligibility_status"] = "not_eligible"
    row["rejection_reason_or_null"] = "explicit_robustness_evidence_not_applied"
    return row


def _screening_artifact(candidate_id: str = "cand_apply") -> dict[str, Any]:
    row = _pilot_row(candidate_id)
    artifact: dict[str, Any] = {
        "run_id": "apply_robustness_test",
        "created_at_utc": "2026-06-23T00:00:00+00:00",
        "code_commit": "test",
        "screening_backend": "vectorbt",
        "vectorbt_version": "1.0.0",
        "vectorbt_engine": "rust",
        "engine_parity_status": "rust_runtime_proven",
        "rust_engine_required_for_scope": False,
        "rust_engine_available": True,
        "vectorbt_engine_runtime_proof": True,
        "license_review": "pilot_license_review_recorded",
        "research_clock": "event_window_pilot",
        "parameter_space_id": "test_parameter_space",
        "parameter_space_hash": "sha256:test_parameter_space",
        "max_trials": 1,
        "trials_run": 1,
        "run_budget_id": "test_budget",
        "max_models": 1,
        "max_symbols": 1,
        "max_feature_sets": 1,
        "max_total_trials": 1,
        "max_wall_clock_seconds": None,
        "max_peak_memory_mb_or_null": None,
        "abort_on_budget_exhaustion": True,
        "screening_scope": "pilot",
        "candidate_ids": [candidate_id],
        "candidate_reasons": {candidate_id: "queued_for_vectorbt_screen"},
        "promoted_ids": [candidate_id],
        "promoted_reasons": {candidate_id: "pass"},
        "rejected_ids": [],
        "rejected_reasons": {},
        "stop_reasons": [],
        "feature_set_id": "fs_v1_apply_test",
        "feature_set_hash": "sha256:fs_v1_apply_test",
        "data_manifest_hash": "sha256:data_manifest",
        "lake_manifest_hash": "sha256:lake_manifest",
        "events_csv_hash_or_not_applicable": "sha256:events_csv",
        "split_scheme_id": "walk_forward_test",
        "no_lookahead_signal_shift_proof": "close-derived signals shifted one executable bar",
        "fees_model_id": "test_fees",
        "slippage_model_id": "test_slippage",
        "bar_construction_id": "ohlcv_1m_from_npz_or_supplied_array",
        "promoted": [row],
        "rejected": [],
    }
    artifact.update(
        build_feature_plane_payload(
            bar_construction_id=str(artifact["bar_construction_id"]),
            feature_set_id=str(artifact["feature_set_id"]),
            feature_set_hash=str(artifact["feature_set_hash"]),
            research_clock=str(artifact["research_clock"]),
            screening_scope=str(artifact["screening_scope"]),
        )
    )
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)
    return artifact


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _passing_evidence(
    artifact: dict[str, Any],
    candidate_id: str,
    *,
    binding_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = artifact["promoted"][0]
    robustness_input = copy.deepcopy(_full_passing_input())
    robustness_input["cscv_matrix"] = robustness_input["cscv_matrix"].tolist()
    binding = {
        "screening_artifact_hash": artifact["screening_artifact_hash"],
        "candidate_id": candidate_id,
        "parameter_values_hash": row["parameter_values_hash"],
        "feature_recipe_hash": row["feature_recipe_hash"],
        "data_manifest_hash": artifact["data_manifest_hash"],
        "lake_manifest_hash": artifact["lake_manifest_hash"],
    }
    if binding_overrides:
        binding.update(binding_overrides)
    return {
        "schema": "hft3_robustness_evidence_inputs_v1",
        "candidates": {
            candidate_id: {
                "binding": binding,
                "source_evidence": {
                    "wfc": {
                        "path": "research_cards/robustness/wfc_apply_test.json",
                        "sha256": "a" * 64,
                    },
                    "cscv": "research_cards/robustness/cscv_apply_test.json#sha256:"
                    + "b" * 64,
                },
                "robustness_input": robustness_input,
                "surface_stability_metrics": _surface_pass(),
                "robustness_gate_scope": "operator_explicit_robustness_evidence",
            }
        },
    }


def test_passing_evidence_writes_valid_eligible_artifact_and_receipt(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence.json"
    out_path = tmp_path / "screening_artifact.robust.json"
    original = _screening_artifact(candidate_id)
    _write_json(screening_path, original)
    _write_json(evidence_path, _passing_evidence(original, candidate_id))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-evidence",
            str(evidence_path),
            "--out",
            str(out_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "ok"
    assert receipt["matched_count"] == 1
    assert receipt["eligible_count"] == 1
    assert receipt["eligible_candidate_ids"] == [candidate_id]
    assert out_path.is_file()

    updated = json.loads(out_path.read_text(encoding="utf-8"))
    validate_screening_artifact(updated)
    assert updated["screening_artifact_hash"] == compute_screening_artifact_hash(updated)
    assert updated["screening_artifact_hash"] != original["screening_artifact_hash"]
    row = updated["promoted"][0]
    assert row["replay_eligibility_status"] == "eligible"
    assert row["rejection_reason_or_null"] is None
    assert row["wfc_status"] == "pass"
    assert row["dsr_status"] == "pass"
    assert row["pbo_status"] == "pass"
    assert row["cscv_status"] == "pass"
    assert row["robustness_artifact_staleness"] == "fresh"
    assert row["robustness_gate_scope"] == "operator_explicit_robustness_evidence"
    receipt_row = row["robustness_evidence_receipt"]
    assert receipt_row["binding"]["screening_artifact_hash"] == original["screening_artifact_hash"]
    assert receipt_row["binding"]["parameter_values_hash"] == row["parameter_values_hash"]
    assert receipt_row["source_evidence"]["wfc"]["sha256"] == "a" * 64
    assert len(receipt_row["evidence_entry_hash"]) == 64
    assert validate_candidate_replay_eligibility(row) == []
    assert receipt["screening_artifact_hash"] == updated["screening_artifact_hash"]


def test_incomplete_evidence_fails_closed_without_writing_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence.json"
    out_path = tmp_path / "should_not_exist.json"
    _write_json(screening_path, _screening_artifact(candidate_id))
    artifact = _screening_artifact(candidate_id)
    _write_json(
        evidence_path,
        {
            "schema": "hft3_robustness_evidence_inputs_v1",
            "candidates": {
                candidate_id: {
                    "binding": {
                        "screening_artifact_hash": artifact["screening_artifact_hash"],
                        "candidate_id": candidate_id,
                        "parameter_values_hash": artifact["promoted"][0]["parameter_values_hash"],
                        "feature_recipe_hash": artifact["promoted"][0]["feature_recipe_hash"],
                        "data_manifest_hash": artifact["data_manifest_hash"],
                        "lake_manifest_hash": artifact["lake_manifest_hash"],
                    },
                    "source_evidence": {
                        "wfc": {
                            "path": "research_cards/robustness/wfc_apply_test.json",
                            "sha256": "a" * 64,
                        }
                    },
                    "robustness_input": {"per_event_expectancies": [0.01, 0.02]},
                    "surface_stability_metrics": _surface_pass(),
                }
            },
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-evidence",
            str(evidence_path),
            "--out",
            str(out_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "eligible_count_below_min" in result.stderr
    assert not out_path.exists()


def test_missing_source_evidence_fails_closed_without_writing_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence_missing_source.json"
    out_path = tmp_path / "should_not_exist.json"
    original = _screening_artifact(candidate_id)
    evidence = _passing_evidence(original, candidate_id)
    evidence["candidates"][candidate_id].pop("source_evidence")
    _write_json(screening_path, original)
    _write_json(evidence_path, evidence)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-evidence",
            str(evidence_path),
            "--out",
            str(out_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "eligible_count_below_min" in result.stderr
    assert not out_path.exists()


def test_wrong_evidence_binding_fails_closed_without_writing_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence_wrong_binding.json"
    out_path = tmp_path / "should_not_exist.json"
    original = _screening_artifact(candidate_id)
    _write_json(screening_path, original)
    _write_json(
        evidence_path,
        _passing_evidence(
            original,
            candidate_id,
            binding_overrides={"parameter_values_hash": "sha256:wrong_parameter_hash"},
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-evidence",
            str(evidence_path),
            "--out",
            str(out_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "eligible_count_below_min" in result.stderr
    assert not out_path.exists()


def test_hbt_validator_rejection_blocks_output(tmp_path: Path, monkeypatch) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence.json"
    out_path = tmp_path / "should_not_exist.json"
    original = _screening_artifact(candidate_id)
    _write_json(screening_path, original)
    _write_json(evidence_path, _passing_evidence(original, candidate_id))
    module = _load_script_module()
    monkeypatch.setattr(
        module,
        "validate_candidate_replay_eligibility",
        lambda _row: ["forced_hbt_validator_rejection"],
    )

    try:
        module.apply_robustness_evidence(
            screening_artifact_path=screening_path,
            robustness_evidence_path=evidence_path,
            out_path=out_path,
            candidate_ids=None,
            min_eligible=1,
        )
    except ValueError as exc:
        assert "eligible_count_below_min" in str(exc)
    else:
        raise AssertionError("expected HBT validator rejection to fail closed")
    assert not out_path.exists()
