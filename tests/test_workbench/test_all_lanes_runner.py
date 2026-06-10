"""All-lane runner terminal-state contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.src.run.all_lanes import TERMINAL_STATES, build_all_lanes_plan, run_all_lanes


REPO = Path(__file__).resolve().parents[2]


def test_build_all_lanes_plan_assigns_one_terminal_state_per_model() -> None:
    plan = build_all_lanes_plan(REPO, "fresh_all_lanes_test")

    assert plan["run_id"] == "fresh_all_lanes_test"
    assert plan["model_count"] == len(plan["models"])
    assert plan["models"]
    for row in plan["models"]:
        assert row["run_id"] == "fresh_all_lanes_test"
        assert row["model_id"]
        assert row["lane"] in {"cme_futures", "crypto", "equities"}
        assert row["terminal_state"] in TERMINAL_STATES
    assert sum(plan["terminal_counts"].values()) == len(plan["models"])


def test_run_all_lanes_writes_run_id_scoped_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.leakage_detector as leakage_module

    monkeypatch.setattr(
        module,
        "build_all_lanes_plan",
        lambda repo, run_id: {
            "schema_version": "workbench_all_lanes_plan_v1",
            "run_id": run_id,
            "generated_at_utc": "2026-06-05T00:00:00Z",
            "repo": str(repo),
            "artifact_reuse_policy": "active_run_id_only",
            "previous_run_artifacts_reused": False,
            "lanes": [{"lane": "crypto", "load_status": "loaded"}],
            "models": [
                {
                    "run_id": run_id,
                    "model_id": "CRYPTO_TEST",
                    "lane": "crypto",
                    "terminal_state": "BLOCKED_VALIDATION",
                    "reason": "planning",
                }
            ],
            "model_count": 1,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": {state: (1 if state == "BLOCKED_VALIDATION" else 0) for state in TERMINAL_STATES},
        },
    )
    monkeypatch.setattr(
        leakage_module,
        "run_leakage_detection",
        lambda repo, run_id=None: {
            "status": "PASS",
            "blocking": [],
            "artifact_paths": {
                "json": str(repo / "runtime" / "workbench" / "all_lanes" / str(run_id) / "leakage_detection.json")
            },
        },
    )

    result = run_all_lanes(tmp_path, "fresh_all_lanes_test")

    assert result["status"] == "PASS"
    run_dir = tmp_path / "runtime" / "workbench" / "all_lanes" / "fresh_all_lanes_test"
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    rejected = json.loads((run_dir / "rejected_stale_artifacts.json").read_text(encoding="utf-8"))
    assert plan["run_id"] == "fresh_all_lanes_test"
    assert summary["run_id"] == "fresh_all_lanes_test"
    assert rejected["run_id"] == "fresh_all_lanes_test"
    assert summary["blocking_gates"][0]["gate"] == "model_execution"


def test_run_all_lanes_preserves_stale_artifact_rejection_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.leakage_detector as leakage_module

    monkeypatch.setattr(
        module,
        "build_all_lanes_plan",
        lambda repo, run_id: {
            "schema_version": "workbench_all_lanes_plan_v1",
            "run_id": run_id,
            "generated_at_utc": "2026-06-05T00:00:00Z",
            "repo": str(repo),
            "artifact_reuse_policy": "active_run_id_only",
            "previous_run_artifacts_reused": False,
            "lanes": [],
            "models": [],
            "model_count": 0,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": {state: 0 for state in TERMINAL_STATES},
        },
    )
    monkeypatch.setattr(
        leakage_module,
        "run_leakage_detection",
        lambda repo, run_id=None: {
            "status": "PASS",
            "blocking": [],
            "artifact_paths": {
                "json": str(repo / "runtime" / "workbench" / "all_lanes" / str(run_id) / "leakage_detection.json")
            },
        },
    )
    run_dir = tmp_path / "runtime" / "workbench" / "all_lanes" / "fresh_all_lanes_test"
    run_dir.mkdir(parents=True)
    (run_dir / "rejected_stale_artifacts.json").write_text(
        json.dumps(
            {
                "schema_version": "rejected_stale_artifacts_v1",
                "run_id": "fresh_all_lanes_test",
                "rows": [{"path": "artifacts/research_cards/workbench_runs/old/summary.json"}],
                "rejected_count": 1,
            }
        ),
        encoding="utf-8",
    )

    run_all_lanes(tmp_path, "fresh_all_lanes_test")

    rejected = json.loads((run_dir / "rejected_stale_artifacts.json").read_text(encoding="utf-8"))
    assert rejected["rejected_count"] == 1
    assert rejected["rows"][0]["path"] == "artifacts/research_cards/workbench_runs/old/summary.json"


def test_run_all_lanes_returns_fail_when_leakage_detector_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.leakage_detector as leakage_module

    monkeypatch.setattr(
        module,
        "build_all_lanes_plan",
        lambda repo, run_id: {
            "schema_version": "workbench_all_lanes_plan_v1",
            "run_id": run_id,
            "generated_at_utc": "2026-06-05T00:00:00Z",
            "repo": str(repo),
            "artifact_reuse_policy": "active_run_id_only",
            "previous_run_artifacts_reused": False,
            "lanes": [],
            "models": [],
            "model_count": 0,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": {state: 0 for state in TERMINAL_STATES},
        },
    )
    monkeypatch.setattr(
        leakage_module,
        "run_leakage_detection",
        lambda repo, run_id=None: {
            "status": "FAIL",
            "blocking": [{"gate": "generated_artifact_roots_clean", "status": "FAIL", "reason": "stale"}],
            "artifact_paths": {
                "json": str(repo / "runtime" / "workbench" / "all_lanes" / str(run_id) / "leakage_detection.json")
            },
        },
    )

    result = run_all_lanes(tmp_path, "fresh_all_lanes_test")

    assert result["status"] == "FAIL"
    summary = json.loads(
        (
            tmp_path
            / "runtime"
            / "workbench"
            / "all_lanes"
            / "fresh_all_lanes_test"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["leakage_detection_status"] == "FAIL"
    assert any(gate["gate"] == "leakage_detection" for gate in summary["blocking_gates"])


def test_run_all_lanes_execute_mode_is_not_silent_fake_execution(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="not wired"):
        run_all_lanes(tmp_path, "fresh_all_lanes_test", execute=True)


def test_build_all_lanes_plan_equities_blocked_endpoint_when_status_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equities-lane models get BLOCKED_ENDPOINT when ibkr_endpoint_status.json is absent."""
    import workbench.src.run.all_lanes as module
    from workbench.src.registry import model_catalog as catalog_module
    from workbench.src.registry import unified_registry as registry_module

    # Patch list_models to return one equities model and one crypto model.
    monkeypatch.setattr(module, "list_models", lambda: ["EQUITIES_TEST_A", "CRYPTO_TEST_B"])
    # Patch load_catalog to return an empty dict.
    monkeypatch.setattr(module, "load_catalog", lambda repo: {})

    # Patch lane resolution: equities prefix → equities, crypto prefix → crypto.
    class _FakeEnum(str):
        @property
        def value(self) -> str:
            return str(self)

    class _FakeRegistry:
        @staticmethod
        def instance():
            return _FakeRegistry()

        def resolve_lane(self, model_id: str) -> _FakeEnum:
            if model_id.startswith("EQUITIES_"):
                return _FakeEnum("equities")
            return _FakeEnum("crypto")

        def all_registrations(self):
            return []

    monkeypatch.setattr(module, "LaneRegistry", _FakeRegistry)
    monkeypatch.setattr(module, "register_all_lanes", lambda: None)

    # No ibkr_endpoint_status.json in tmp_path → endpoint not ready.
    plan = build_all_lanes_plan(tmp_path, "ep_test_missing")

    equities_rows = [r for r in plan["models"] if r["lane"] == "equities"]
    crypto_rows = [r for r in plan["models"] if r["lane"] == "crypto"]
    assert equities_rows, "expected at least one equities row"
    for row in equities_rows:
        assert row["terminal_state"] == "BLOCKED_ENDPOINT", row
        assert "IBKR Web API endpoint not ready" in row["reason"]
    for row in crypto_rows:
        assert row["terminal_state"] == "BLOCKED_VALIDATION", row


def test_build_all_lanes_plan_equities_blocked_validation_when_ready_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equities-lane models get BLOCKED_VALIDATION (planning default) when ready=true artifact present."""
    import json as _json

    import workbench.src.run.all_lanes as module
    from workbench.src.registry import model_catalog as catalog_module
    from workbench.src.registry import unified_registry as registry_module

    monkeypatch.setattr(module, "list_models", lambda: ["EQUITIES_TEST_A"])
    monkeypatch.setattr(module, "load_catalog", lambda repo: {})

    class _FakeEnum(str):
        @property
        def value(self) -> str:
            return str(self)

    class _FakeRegistry:
        @staticmethod
        def instance():
            return _FakeRegistry()

        def resolve_lane(self, model_id: str) -> _FakeEnum:
            return _FakeEnum("equities")

        def all_registrations(self):
            return []

    monkeypatch.setattr(module, "LaneRegistry", _FakeRegistry)
    monkeypatch.setattr(module, "register_all_lanes", lambda: None)

    # Write ibkr_endpoint_status.json with ready=true.
    status_path = tmp_path / "runtime" / "equities_lane" / "ibkr_endpoint_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(_json.dumps({"ready": True}), encoding="utf-8")

    plan = build_all_lanes_plan(tmp_path, "ep_test_ready")

    for row in plan["models"]:
        assert row["terminal_state"] == "BLOCKED_VALIDATION", row
        assert "IBKR" not in row["reason"]
