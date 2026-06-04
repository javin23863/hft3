"""Central Workbench leakage detector tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.src.run.leakage_detector import run_leakage_detection


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _active_run(repo: Path, run_id: str, rejected: dict | None = None) -> Path:
    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id
    _write_json(
        repo / "runtime" / "workbench" / "active_run.json",
        {
            "schema_version": "workbench_active_run_v1",
            "run_id": run_id,
            "source": "all_lanes",
            "artifact_reuse_policy": "active_run_id_only",
        },
    )
    _write_json(run_dir / "plan.json", {"run_id": run_id})
    _write_json(run_dir / "summary.json", {"run_id": run_id})
    _write_json(
        run_dir / "rejected_stale_artifacts.json",
        rejected
        or {
            "schema_version": "rejected_stale_artifacts_v1",
            "run_id": run_id,
            "rows": [],
            "rejected_count": 0,
        },
    )
    return run_dir


def _patch_snapshot_loader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fabric_gate: str = "PASS",
    pit_status: str = "PASS",
    stale_sources_blocked: bool = True,
) -> None:
    import workbench.src.run.leakage_detector as module

    def fake_load_run_evidence(_repo: Path, source: str):
        if source == "all_lanes":
            return SimpleNamespace(
                source="all_lanes",
                run_id="fresh_all_lanes_test",
                current_stage="model_execution_plan",
                diagnostics={
                    "feature_fabric": {
                        "gate_status": fabric_gate,
                        "pit_validation_status": pit_status,
                        "blocking_gates": [] if fabric_gate == "PASS" else [{"gate": "feature_fabric"}],
                        "pit_issue_count": 0 if pit_status == "PASS" else 1,
                    }
                },
            )
        return SimpleNamespace(
            source=source,
            run_id="old_run",
            current_stage="stale_source_blocked" if stale_sources_blocked else "loaded",
            diagnostics={},
        )

    monkeypatch.setattr(module, "load_run_evidence", fake_load_run_evidence)


def test_leakage_detector_passes_clean_active_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "fresh_all_lanes_test"
    _active_run(tmp_path, run_id)
    _patch_snapshot_loader(monkeypatch)

    result = run_leakage_detection(tmp_path, run_id=run_id, tracked_paths_fn=lambda _repo: set())

    assert result["status"] == "PASS"
    assert result["blocking"] == []
    assert (tmp_path / "runtime" / "workbench" / "all_lanes" / run_id / "leakage_detection.json").is_file()
    assert (tmp_path / "runtime" / "workbench" / "all_lanes" / run_id / "leakage_detection.md").is_file()


def test_leakage_detector_fails_untracked_generated_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "fresh_all_lanes_test"
    _active_run(tmp_path, run_id)
    stale = tmp_path / "research_cards" / "pipeline_runs" / "old" / "summary.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    _patch_snapshot_loader(monkeypatch)

    result = run_leakage_detection(tmp_path, run_id=run_id, tracked_paths_fn=lambda _repo: set())

    assert result["status"] == "FAIL"
    assert any(blocker["gate"] == "generated_artifact_roots_clean" for blocker in result["blocking"])


def test_leakage_detector_fails_when_feature_fabric_pit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "fresh_all_lanes_test"
    _active_run(tmp_path, run_id)
    _patch_snapshot_loader(monkeypatch, pit_status="FAIL")

    result = run_leakage_detection(tmp_path, run_id=run_id, tracked_paths_fn=lambda _repo: set())

    assert result["status"] == "FAIL"
    assert any(blocker["gate"] == "feature_fabric_pit_validation" for blocker in result["blocking"])


def test_leakage_detector_accepts_explicitly_quarantined_tracked_stale_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "fresh_all_lanes_test"
    tracked_path = "artifacts/research_cards/workbench_runs/old/summary.json"
    _active_run(
        tmp_path,
        run_id,
        rejected={
            "schema_version": "rejected_stale_artifacts_v1",
            "run_id": run_id,
            "rows": [
                {
                    "path": tracked_path,
                    "target": "artifacts/research_cards/workbench_runs",
                    "status": "REJECTED",
                    "reason": "tracked_generated_artifact_outside_active_run_boundary",
                }
            ],
            "rejected_count": 1,
        },
    )
    stale = tmp_path / tracked_path
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    _patch_snapshot_loader(monkeypatch)

    result = run_leakage_detection(tmp_path, run_id=run_id, tracked_paths_fn=lambda _repo: {tracked_path})

    assert result["status"] == "PASS"
    stale_check = next(check for check in result["checks"] if check["name"] == "tracked_stale_artifacts_quarantined")
    assert stale_check["rejected_count"] == 1
