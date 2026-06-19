"""Phase 9: feature-family paid-screen gate tests."""

from __future__ import annotations

import json
from pathlib import Path

from backtest_pipeline.src.feature_family_status import (
    evaluate_feature_family_paid_gate,
    load_feature_family_status_manifest,
)
from backtest_pipeline.src.feature_plane import build_feature_plane_payload
from scripts.validate_paid_screen_ready_gate import evaluate_gate

_REPO = Path(__file__).resolve().parents[2]


def _pilot_artifact(*, recipe_hash: str = "abc123", screening_scope: str = "pilot") -> dict:
    plane = build_feature_plane_payload(
        bar_construction_id="fs_v1_row_loop_from_feature_store",
        feature_set_id="fs_v1",
        feature_set_hash="sha256:test",
        research_clock="scheduled_event",
        screening_scope=screening_scope,
    )
    return {
        "screening_backend": "vectorbt",
        "screening_scope": screening_scope,
        "trials_run": 1,
        "promoted": [
            {
                "candidate_id": "c1",
                "screening_status": "pass",
                "feature_recipe_hash": recipe_hash,
                "vectorbt_results": {
                    "feature_recipe_hash": recipe_hash,
                    "pilot_gate_evaluation": {"failures": []},
                },
            }
        ],
        **plane,
    }


def test_load_feature_family_status_manifest() -> None:
    manifest = load_feature_family_status_manifest(_REPO)
    assert manifest.get("schema_version") == "feature_family_status.v1"
    gate = manifest.get("paid_screen_gate") or {}
    assert gate.get("allowed") is True
    assert "feature_recipe_hash" in (gate.get("required_pilot_fields") or [])


def test_paid_gate_fails_when_manifest_disallows() -> None:
    pilot = _pilot_artifact()
    manifest = load_feature_family_status_manifest(_REPO)
    gate = dict(manifest.get("paid_screen_gate") or {})
    gate["allowed"] = False
    errors, summary = evaluate_feature_family_paid_gate(
        pilot,
        repo_root=_REPO,
        status_manifest={**manifest, "paid_screen_gate": gate},
    )
    assert any(err.startswith("paid_screen_gate_not_allowed:") for err in errors)
    assert summary["paid_screen_gate_allowed"] is False
    assert summary["resolved_fields"]["feature_recipe_hash"] == "abc123"


def test_paid_gate_missing_recipe_hash() -> None:
    pilot = _pilot_artifact(recipe_hash="")
    pilot["promoted"] = [{"candidate_id": "c1", "screening_status": "pass"}]
    errors, summary = evaluate_feature_family_paid_gate(pilot, repo_root=_REPO)
    assert "pilot_missing:feature_recipe_hash" in errors
    assert summary["resolved_fields"]["feature_recipe_hash"] is None


def test_paid_gate_resolves_family_status_fields() -> None:
    pilot = _pilot_artifact()
    _errors, summary = evaluate_feature_family_paid_gate(pilot, repo_root=_REPO)
    resolved = summary["resolved_fields"]
    assert resolved["feature_usage_manifest"] == "present"
    assert resolved["cross_asset_alignment_status"]
    assert resolved["robustness_result"] == "not_run_pilot_scope"
    assert resolved["hftbacktest_handoff_status"] == "recipe_hash_handoff_ready"
    assert resolved["vectorbt_result"] == "screen_pass"


def test_ready_gate_includes_feature_family_summary(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot.json"
    pilot = _pilot_artifact()
    pilot.update(
        {
            "no_lookahead_signal_shift_proof": {"status": "pass"},
            "events_csv_hash_or_not_applicable": "hash1",
            "lake_manifest_hash": "hash2",
        }
    )
    pilot_path.write_text(json.dumps(pilot), encoding="utf-8")

    smoke_manifest = tmp_path / "smoke.json"
    unit_dir = tmp_path / "units" / "u1"
    unit_dir.mkdir(parents=True)
    (unit_dir / "screening_artifact.json").write_text(json.dumps(pilot), encoding="utf-8")
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [
                    {
                        "unit_id": "u1",
                        "status": "OK",
                        "screening_artifact_relpath": "units/u1/screening_artifact.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_gate(
        pilot_artifact=pilot_path,
        smoke_manifest=smoke_manifest,
        repo_root=_REPO,
        run_pytest=False,
    )
    assert "feature_family_gate" in result
    assert result["feature_family_gate"]["resolved_fields"]["feature_recipe_hash"] == "abc123"
    assert result["feature_family_gate"]["paid_screen_gate_allowed"] is True
    assert not any("paid_screen_gate_not_allowed" in err for err in result["errors"])
