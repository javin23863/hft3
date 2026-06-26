from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.run_research_pipeline import run_pipeline


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_spec(tmp_path: Path, *, target_stage: str = "stage_1_vectorbt_screen") -> dict:
    events = tmp_path / "events.csv"
    features = tmp_path / "features"
    events.write_text("event_id\n", encoding="utf-8")
    features.mkdir()
    return {
        "version": 1,
        "run_id": "unit_pipeline",
        "repo_root": str(tmp_path),
        "bundle_root": str(tmp_path / "research_cards" / "pipeline_runs"),
        "target_stage": target_stage,
        "preflight": {
            "require_vault_gate": False,
            "required_paths": {
                "events_csv": str(events),
                "feature_store_root": str(features),
            },
        },
        "stages": {},
    }


def test_pipeline_blocks_vectorbt_artifact_with_stub_and_zero_promotions(tmp_path: Path) -> None:
    artifact = _write_json(
        tmp_path / "bad_screening_artifact.json",
        {
            "promoted_ids": [],
            "rejected": [
                {
                    "metric_values": {
                        "vbt_stats": {
                            "Total Trades": 0,
                        }
                    }
                }
            ],
            "feature_plane_status": "bar_stub_research_only",
            "feature_set_id": "fs_v1_pilot_unknown",
            "bar_construction_id": "ohlcv_1m_from_npz_or_supplied_array",
        },
    )
    spec = _base_spec(tmp_path)
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "outputs": {"screening_artifact": str(artifact)},
        }
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "blocked"
    failures = "\n".join(bundle["failures"])
    assert "zero_promoted_ids" in failures
    assert "zero_positive_trade_rows" in failures
    assert "forbidden_feature_plane_status:bar_stub_research_only" in failures
    assert "forbidden_feature_set_id:fs_v1_pilot_unknown" in failures
    assert "forbidden_bar_construction_id:ohlcv_1m_from_npz_or_supplied_array" in failures


def test_runner_blocks_unknown_feature_set_even_when_feature_plane_allowed(tmp_path: Path) -> None:
    artifact = _write_json(
        tmp_path / "unknown_feature_set_artifact.json",
        {
            "promoted_ids": ["cand_1"],
            "promoted": [{"candidate_id": "cand_1", "vectorbt_results": {"num_trades": 4}}],
            "feature_plane_status": "scheduled_event_only",
            "feature_set_id": "fs_v1_pilot_unknown",
            "bar_construction_id": "fs_v1_row_loop_from_feature_store",
        },
    )
    spec = _base_spec(tmp_path)
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "outputs": {"screening_artifact": str(artifact)},
        }
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "blocked"
    assert "stage_1_vectorbt_screen:forbidden_feature_set_id:fs_v1_pilot_unknown:n=1" in bundle[
        "failures"
    ]


def test_pipeline_advances_to_promoted_bundle_with_real_evidence(tmp_path: Path) -> None:
    artifact = _write_json(
        tmp_path / "screening_artifact.json",
        {
            "promoted_ids": ["cand_1"],
            "promoted_count": 1,
            "promoted": [
                {
                    "candidate_id": "cand_1",
                    "vectorbt_results": {
                        "num_trades": 12,
                        "gate_metric_authority": "official_vectorbt_portfolio_stats",
                    },
                    "metric_values": {
                        "vbt_stats": {
                            "Total Trades": 12,
                        }
                    },
                }
            ],
            "feature_plane_status": "scheduled_event_only",
            "bar_construction_id": "fs_v1_row_loop_from_feature_store",
        },
    )
    promoted = _write_json(
        tmp_path / "promoted_candidates.json",
        {"promoted_id_count": 1, "promoted_ids": ["cand_1"]},
    )
    spec = _base_spec(tmp_path, target_stage="stage_2_promoted_aggregation")
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "outputs": {"screening_artifact": str(artifact)},
        },
        "stage_2_promoted_aggregation": {
            "outputs": {"promoted_candidates": str(promoted)},
        },
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "ready"
    assert [r["stage_id"] for r in bundle["stage_receipts"]] == [
        "stage_0_ontology",
        "stage_1_vectorbt_screen",
        "stage_2_promoted_aggregation",
    ]
    status = json.loads(
        (tmp_path / "research_cards" / "pipeline_runs" / "unit_pipeline" / "status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["next_stage"] is None
    assert status["last_passed_stage"] == "stage_2_promoted_aggregation"
    vectorbt_receipt = bundle["stage_receipts"][1]
    assert vectorbt_receipt["metrics"]["positive_trade_rows"] == 1


def test_robustness_bridge_blocks_zero_replay_eligible_rows(tmp_path: Path) -> None:
    artifact = _write_json(
        tmp_path / "screening_artifact.json",
        {
            "promoted_ids": ["cand_1"],
            "promoted": [{"candidate_id": "cand_1", "vectorbt_results": {"num_trades": 8}}],
            "feature_plane_status": "scheduled_event_only",
            "bar_construction_id": "fs_v1_row_loop_from_feature_store",
        },
    )
    promoted = _write_json(
        tmp_path / "promoted_candidates.json",
        {"promoted_id_count": 1, "promoted_ids": ["cand_1"]},
    )
    receipt = _write_json(tmp_path / "robustness_evidence_receipt.json", {"status": "ready"})
    applied = _write_json(
        tmp_path / "applied_screening_artifact.json",
        {
            "promoted": [
                {
                    "candidate_id": "cand_1",
                    "replay_eligibility_status": "blocked",
                }
            ]
        },
    )
    spec = _base_spec(tmp_path, target_stage="stage_2_robustness_evidence")
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "outputs": {"screening_artifact": str(artifact)},
        },
        "stage_2_promoted_aggregation": {
            "outputs": {"promoted_candidates": str(promoted)},
        },
        "stage_2_robustness_evidence": {
            "outputs": {
                "robustness_evidence_receipt": str(receipt),
                "applied_screening_artifact": str(applied),
            }
        },
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "blocked"
    assert "stage_2_robustness_evidence:zero_replay_eligible_rows_after_robustness" in bundle[
        "failures"
    ]


def test_resume_uses_existing_passed_receipt_without_rerunning_command(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    artifact = tmp_path / "screening_artifact.json"
    payload = json.dumps(
        {
            "promoted_ids": ["cand"],
            "promoted": [{"vectorbt_results": {"num_trades": 1}}],
            "feature_plane_status": "scheduled_event_only",
            "bar_construction_id": "fs_v1_row_loop_from_feature_store",
        }
    )
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import json; "
            f"Path(r'{marker}').write_text('ran', encoding='utf-8'); "
            f"Path(r'{artifact}').write_text({payload!r}, encoding='utf-8')"
        ),
    ]
    spec = _base_spec(tmp_path)
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "command": command,
            "outputs": {"screening_artifact": str(artifact)},
        }
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    first = run_pipeline(spec_path)
    assert first["status"] == "ready"
    assert marker.read_text(encoding="utf-8") == "ran"
    marker.unlink()

    second = run_pipeline(spec_path, resume=True)

    assert second["status"] == "ready"
    assert not marker.exists()


def test_string_command_does_not_pass_with_preexisting_output(tmp_path: Path) -> None:
    artifact = _write_json(
        tmp_path / "screening_artifact.json",
        {
            "promoted_ids": ["cand"],
            "promoted": [{"vectorbt_results": {"num_trades": 1}}],
            "feature_plane_status": "scheduled_event_only",
            "bar_construction_id": "fs_v1_row_loop_from_feature_store",
        },
    )
    spec = _base_spec(tmp_path)
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "command": f"{sys.executable} -c pass",
            "outputs": {"screening_artifact": str(artifact)},
        }
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "blocked"
    assert (
        "stage_1_vectorbt_screen:stage_command_must_be_non_empty_string_list"
        in bundle["failures"]
    )


def test_malformed_json_output_writes_error_receipt(tmp_path: Path) -> None:
    artifact = tmp_path / "screening_artifact.json"
    artifact.write_text("{", encoding="utf-8")
    spec = _base_spec(tmp_path)
    spec["stages"] = {
        "stage_1_vectorbt_screen": {
            "outputs": {"screening_artifact": str(artifact)},
        }
    }
    spec_path = _write_json(tmp_path / "spec.json", spec)

    bundle = run_pipeline(spec_path)

    assert bundle["status"] == "blocked"
    assert any(
        failure.startswith("stage_1_vectorbt_screen:stage_validation_error:JSONDecodeError")
        for failure in bundle["failures"]
    )
    receipt_path = (
        tmp_path
        / "research_cards"
        / "pipeline_runs"
        / "unit_pipeline"
        / "receipts"
        / "stage_1_vectorbt_screen.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "error"
    assert receipt["completed_at"]
    assert any(
        error.startswith("stage_validation_error:JSONDecodeError")
        for error in receipt["validation_errors"]
    )
