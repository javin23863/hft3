"""All-lane runner terminal-state contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.src.run.all_lanes import TERMINAL_STATES, build_all_lanes_plan, run_all_lanes


REPO = Path(__file__).resolve().parents[2]


def _write_valid_q001_owner_decision(repo: Path) -> None:
    path = repo / "docs" / "project" / "q001_owner_decision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "question_id": "Q001",
                "decision_date": "2026-06-15",
                "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
                "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
                "options_strict_mbo_warning_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
                "available_data_research_allowed": True,
                "accepted_evidence": {
                    "missing_or_unavailable_slots": 211,
                    "strict_mbo_gap_count": 507,
                    "strict_mbo_stale_gap_count": 503,
                    "options_warn_checks": ["options-fixing-mbo-coverage"],
                },
                "model_gap_policy": {
                    "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                    "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                    "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
                    "must_emit_skip_or_rejection_reasons": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _patch_single_model_plan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_id: str,
    lane: str,
    required_datasets: list[str],
    display_name: str = "Test model",
) -> None:
    import workbench.src.run.all_lanes as module

    monkeypatch.setattr(module, "list_models", lambda: [model_id])
    monkeypatch.setattr(
        module,
        "build_models_config",
        lambda: {
            model_id: SimpleNamespace(
                kind="hypothesis",
                required_datasets=required_datasets,
                min_history_years=10,
                robustness_window="discovery",
                latency_lane="sub_10ms",
                execution_assumptions="limit_queue",
                parameter_bounds={},
                signal_field="",
                diagnostics_only=False,
                hyp_id=1,
            )
        },
    )
    monkeypatch.setattr(
        module,
        "load_catalog",
        lambda repo: {
            model_id: SimpleNamespace(
                role="alpha",
                display_name=display_name,
            )
        },
    )

    class _FakeEnum(str):
        @property
        def value(self) -> str:
            return str(self)

    class _FakeRegistry:
        @staticmethod
        def instance():
            return _FakeRegistry()

        def resolve_lane(self, resolved_model_id: str) -> _FakeEnum:
            assert resolved_model_id == model_id
            return _FakeEnum(lane)

        def all_registrations(self):
            return []

    monkeypatch.setattr(module, "LaneRegistry", _FakeRegistry)
    monkeypatch.setattr(module, "register_all_lanes", lambda: None)


def test_build_all_lanes_plan_assigns_one_terminal_state_per_model() -> None:
    plan = build_all_lanes_plan(REPO, "fresh_all_lanes_test")

    assert plan["run_id"] == "fresh_all_lanes_test"
    assert plan["model_count"] == len(plan["models"])
    assert plan["registered_lane_count"] == len(plan["lanes"])
    assert set(plan["lane_model_counts"]) == {lane["lane"] for lane in plan["lanes"]}
    assert sum(plan["lane_model_counts"].values()) == len(plan["models"])
    assert plan["models"]
    for row in plan["models"]:
        assert row["run_id"] == "fresh_all_lanes_test"
        assert row["model_id"]
        assert row["lane"] in {"cme_futures", "equities", "cme_options"}
        assert "campaign_mode" in row
        assert row["kind"] in {"hypothesis", "pdf", "reinforcement_learning", "lane_structural", ""}
        assert isinstance(row["required_datasets"], list)
        assert "latency_lane" in row
        assert "execution_assumptions" in row
        assert isinstance(row["parameter_bounds"], dict)
        assert row["terminal_state"] in TERMINAL_STATES
    assert sum(plan["terminal_counts"].values()) == len(plan["models"])
    for lane, count in plan["lane_model_counts"].items():
        matching_gates = [gate for gate in plan["lane_coverage_gates"] if gate.get("lane") == lane]
        if count == 0:
            assert matching_gates
        else:
            assert not matching_gates
    rl_rows = [row for row in plan["models"] if row["kind"] == "reinforcement_learning"]
    assert rl_rows
    assert {row["terminal_state"] for row in rl_rows} == {"BLOCKED_VALIDATION"}


def test_build_all_lanes_plan_routes_options_campaign_binding_to_options_lane() -> None:
    plan = build_all_lanes_plan(REPO, "fresh_all_lanes_options_binding_test")
    row = next(model for model in plan["models"] if model["model_id"] == "DEALER_HEDGING")

    assert row["campaign_mode"] == "options_lane"
    assert row["lane"] == "equities"
    assert row["required_datasets"] == ["options_chain"]
    assert row["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert row["reason_code"] == "q001_strict_options_missing_data_sidelined"
    assert plan["lane_model_counts"]["equities"] == 1


def test_build_all_lanes_plan_tracks_structural_cme_options_model() -> None:
    plan = build_all_lanes_plan(REPO, "fresh_all_lanes_cme_options_structural_test")
    row = next(model for model in plan["models"] if model["model_id"] == "FOPT_ES_CALL")

    assert row["lane"] == "cme_options"
    assert row["kind"] == "lane_structural"
    assert row["role"] == "options_standalone"
    assert row["model_source"] == "cme_options_lane_registration_structural_fopt"
    assert row["required_datasets"] == ["options_chain", "strict_options_quotes", "options_quote_mbo"]
    assert row["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert row["reason_code"] == "q001_strict_options_missing_data_sidelined"
    assert row["missing_data_policy"] == "SIDELINE_UNTIL_DATA_FILLED"
    assert row["skip_or_rejection_required"] is True
    assert "available_data_policy" not in row
    assert plan["lane_model_counts"]["cme_options"] == 1
    assert not [gate for gate in plan["lane_coverage_gates"] if gate.get("lane") == "cme_options"]


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
            "registered_lane_count": 1,
            "lane_model_counts": {"crypto": 1, "cme_options": 0},
            "lane_coverage_gates": [
                {
                    "gate": "lane_model_universe",
                    "status": "BLOCKING",
                    "lane": "cme_options",
                    "reason": "missing",
                    "model_count": 0,
                }
            ],
            "model_universe_status": "BLOCKING",
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
    assert summary["lane_model_counts"] == {"crypto": 1, "cme_options": 0}
    assert summary["lane_coverage_gates"][0]["lane"] == "cme_options"
    assert summary["blocking_gates"][0]["gate"] == "lane_model_universe"
    assert summary["blocking_gates"][1]["gate"] == "model_execution"


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
            "registered_lane_count": 0,
            "lane_model_counts": {},
            "lane_coverage_gates": [],
            "model_universe_status": "PLANNED",
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
            "registered_lane_count": 0,
            "lane_model_counts": {},
            "lane_coverage_gates": [],
            "model_universe_status": "PLANNED",
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


def test_run_all_lanes_execute_dispatches_eligible_available_data_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.campaign_runner as campaign_module
    import workbench.src.run.leakage_detector as leakage_module

    terminal_counts = {state: 0 for state in TERMINAL_STATES}
    terminal_counts["BLOCKED_VALIDATION"] = 1
    terminal_counts["BLOCKED_MISSING_DATA"] = 1
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
            "lanes": [{"lane": "cme_futures", "load_status": "loaded", "symbols": ["ES.v.0"]}],
            "registered_lane_count": 1,
            "lane_model_counts": {"cme_futures": 2},
            "lane_coverage_gates": [],
            "model_universe_status": "PLANNED",
            "models": [
                {
                    "run_id": run_id,
                    "model_id": "CME_AVAILABLE_ALPHA",
                    "lane": "cme_futures",
                    "symbol": "ES.v.0",
                    "terminal_state": "BLOCKED_VALIDATION",
                    "reason": "planning",
                    "available_data_policy": "RUN_WITH_EXPLICIT_COVERAGE",
                    "execution_eligible": True,
                    "execution_block_reason": "",
                },
                {
                    "run_id": run_id,
                    "model_id": "FOPT_STRICT_QUOTES",
                    "lane": "cme_futures",
                    "terminal_state": "BLOCKED_MISSING_DATA",
                    "reason": "strict missing data",
                    "missing_data_policy": "SIDELINE_UNTIL_DATA_FILLED",
                    "execution_eligible": False,
                    "execution_block_reason": "q001_strict_options_missing_data_sidelined",
                },
            ],
            "model_count": 2,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": terminal_counts,
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
    calls: list[dict[str, object]] = []

    def _fake_run_campaign(repo: Path, model_id: str, symbol: str, **kwargs: object) -> SimpleNamespace:
        calls.append({"repo": repo, "model_id": model_id, "symbol": symbol, "kwargs": kwargs})
        return SimpleNamespace(
            campaign_id=kwargs["campaign_id"],
            model_id=model_id,
            symbol=symbol,
            status="PASS",
            param_hash="abc123",
            periods=[SimpleNamespace(events_run=1)],
            artifact_dir=str(repo / "research_cards" / "workbench_runs" / str(kwargs["campaign_id"])),
        )

    monkeypatch.setattr(campaign_module, "run_campaign", _fake_run_campaign)

    result = run_all_lanes(tmp_path, "fresh_all_lanes_test", execute=True)

    assert result["status"] == "PASS"
    assert len(calls) == 1
    assert calls[0]["repo"] == tmp_path
    assert calls[0]["model_id"] == "CME_AVAILABLE_ALPHA"
    assert calls[0]["symbol"] == "ES.v.0"
    assert calls[0]["kwargs"] == {
        "dry_run": False,
        "download_missing": False,
        "allow_partial": True,
        "trial_mode": False,
        "campaign_id": "all_lanes_fresh_all_lanes_test_CME_AVAILABLE_ALPHA_ES.v.0",
    }
    run_dir = tmp_path / "runtime" / "workbench" / "all_lanes" / "fresh_all_lanes_test"
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    executed_row = next(row for row in plan["models"] if row["model_id"] == "CME_AVAILABLE_ALPHA")
    strict_row = next(row for row in plan["models"] if row["model_id"] == "FOPT_STRICT_QUOTES")
    assert executed_row["terminal_state"] == "EXECUTED"
    assert executed_row["campaign_status"] == "PASS"
    assert executed_row["campaign_id"] == "all_lanes_fresh_all_lanes_test_CME_AVAILABLE_ALPHA_ES.v.0"
    assert executed_row["artifact_dir"].endswith("all_lanes_fresh_all_lanes_test_CME_AVAILABLE_ALPHA_ES.v.0")
    assert strict_row["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert "campaign_id" not in strict_row
    assert plan["terminal_counts"]["EXECUTED"] == 1
    assert plan["terminal_counts"]["BLOCKED_MISSING_DATA"] == 1
    assert summary["decision_action"] == "BLOCKED"
    assert summary["state"] == "executed"
    assert summary["execution_results"] == plan["execution_results"]
    assert [row["status"] for row in summary["execution_results"]] == ["EXECUTED", "SKIPPED"]


def test_run_all_lanes_execute_skips_missing_explicit_workbench_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.campaign_runner as campaign_module
    import workbench.src.run.leakage_detector as leakage_module

    terminal_counts = {state: 0 for state in TERMINAL_STATES}
    terminal_counts["BLOCKED_VALIDATION"] = 1
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
            "lanes": [{"lane": "cme_futures", "load_status": "loaded", "symbols": ["ES", "MES"]}],
            "registered_lane_count": 1,
            "lane_model_counts": {"cme_futures": 1},
            "lane_coverage_gates": [],
            "model_universe_status": "PLANNED",
            "models": [
                {
                    "run_id": run_id,
                    "model_id": "CME_AVAILABLE_ALPHA",
                    "lane": "cme_futures",
                    "terminal_state": "BLOCKED_VALIDATION",
                    "reason": "planning",
                    "available_data_policy": "RUN_WITH_EXPLICIT_COVERAGE",
                    "execution_eligible": True,
                    "execution_block_reason": "missing_explicit_workbench_symbol",
                }
            ],
            "model_count": 1,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": terminal_counts,
        },
    )
    monkeypatch.setattr(
        leakage_module,
        "run_leakage_detection",
        lambda repo, run_id=None: {"status": "PASS", "blocking": [], "artifact_paths": {"json": ""}},
    )
    symbols: list[str] = []

    def _fake_run_campaign(repo: Path, model_id: str, symbol: str, **kwargs: object) -> SimpleNamespace:
        symbols.append(symbol)
        return SimpleNamespace(
            campaign_id=kwargs["campaign_id"],
            model_id=model_id,
            symbol=symbol,
            status="PASS",
            param_hash="abc123",
            artifact_dir=str(repo / "research_cards" / "workbench_runs" / str(kwargs["campaign_id"])),
        )

    monkeypatch.setattr(campaign_module, "run_campaign", _fake_run_campaign)

    run_all_lanes(tmp_path, "fresh_all_lanes_raw_symbols", execute=True)

    assert symbols == []
    summary = json.loads(
        (
            tmp_path
            / "runtime"
            / "workbench"
            / "all_lanes"
            / "fresh_all_lanes_raw_symbols"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["state"] == "skipped"
    assert summary["execution_results"][0]["reason"] == "missing_explicit_workbench_symbol"


def test_run_all_lanes_execute_blocks_before_campaign_when_leakage_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workbench.src.run.all_lanes as module
    import workbench.src.run.campaign_runner as campaign_module
    import workbench.src.run.leakage_detector as leakage_module

    terminal_counts = {state: 0 for state in TERMINAL_STATES}
    terminal_counts["BLOCKED_VALIDATION"] = 1
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
            "lanes": [{"lane": "cme_futures", "load_status": "loaded", "symbols": ["MES.v.0"]}],
            "registered_lane_count": 1,
            "lane_model_counts": {"cme_futures": 1},
            "lane_coverage_gates": [],
            "model_universe_status": "PLANNED",
            "models": [
                {
                    "run_id": run_id,
                    "model_id": "CME_AVAILABLE_ALPHA",
                    "lane": "cme_futures",
                    "symbol": "MES.v.0",
                    "terminal_state": "BLOCKED_VALIDATION",
                    "reason": "planning",
                    "available_data_policy": "RUN_WITH_EXPLICIT_COVERAGE",
                    "execution_eligible": True,
                    "execution_block_reason": "",
                }
            ],
            "model_count": 1,
            "terminal_states": sorted(TERMINAL_STATES),
            "terminal_counts": terminal_counts,
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

    def _unexpected_campaign(*args: object, **kwargs: object) -> None:
        raise AssertionError("campaign must not start when leakage detection fails")

    monkeypatch.setattr(campaign_module, "run_campaign", _unexpected_campaign)

    result = run_all_lanes(tmp_path, "fresh_all_lanes_leakage_blocks", execute=True)

    assert result["status"] == "FAIL"
    summary = json.loads(
        (
            tmp_path
            / "runtime"
            / "workbench"
            / "all_lanes"
            / "fresh_all_lanes_leakage_blocks"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["state"] == "blocked"
    assert summary["execution_results"] == []
    assert summary["blocking_gates"][0]["gate"] == "model_execution"
    assert summary["blocking_gates"][0]["status"] == "BLOCKED"


def test_build_all_lanes_plan_equities_uses_planning_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equities-lane (options/parity) models get the BLOCKED_VALIDATION planning default."""
    import workbench.src.run.all_lanes as module

    monkeypatch.setattr(module, "list_models", lambda: ["OPTIONS_TEST_A"])
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

    plan = build_all_lanes_plan(tmp_path, "ep_test_planning_default")

    for row in plan["models"]:
        assert row["terminal_state"] == "BLOCKED_VALIDATION", row
        assert "IBKR" not in row["reason"]


@pytest.mark.parametrize(
    "required_dataset",
    [
        "options_chain",
        "strict_options_quotes",
        "strict_mbo_quotes",
        "options_order_book",
        "options_quote_mbo",
        "l2_order_book",
    ],
)
def test_build_all_lanes_plan_strict_options_model_blocks_missing_data(
    required_dataset: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_valid_q001_owner_decision(tmp_path)
    _patch_single_model_plan(
        monkeypatch,
        model_id="FOPT_STRICT_CHAIN",
        lane="cme_options",
        required_datasets=[required_dataset],
        display_name="FOPT strict options chain",
    )

    plan = build_all_lanes_plan(tmp_path, "q001_strict_options")
    row = plan["models"][0]

    assert row["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert row["reason_code"] == "q001_strict_options_missing_data_sidelined"
    assert row["missing_data_policy"] == "SIDELINE_UNTIL_DATA_FILLED"
    assert row["skip_or_rejection_required"] is True
    assert "docs/project/q001_owner_decision.json" in row["authority_refs"]
    assert "available_data_policy" not in row
    assert plan["model_universe_status"] == "PLANNED"
    assert plan["model_gap_gates"] == []
    assert plan["terminal_counts"]["BLOCKED_MISSING_DATA"] == 1
    assert plan["terminal_counts"]["BLOCKED_VALIDATION"] == 0


def test_build_all_lanes_plan_normal_mbo_npz_model_remains_validation_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_valid_q001_owner_decision(tmp_path)
    _patch_single_model_plan(
        monkeypatch,
        model_id="CME_MBO_ALPHA",
        lane="cme_futures",
        required_datasets=["mbo_npz"],
    )

    plan = build_all_lanes_plan(tmp_path, "q001_normal_mbo")
    row = plan["models"][0]

    assert row["terminal_state"] == "BLOCKED_VALIDATION"
    assert row["reason"] == "All-lane dry-run planning emitted no execution evidence yet."
    assert row["available_data_policy"] == "RUN_WITH_EXPLICIT_COVERAGE"
    assert row["skip_or_rejection_required"] is True
    assert "docs/project/q001_owner_decision.json" in row["authority_refs"]
    assert "reason_code" not in row
    assert row["execution_eligible"] is False
    assert row["execution_block_reason"] == "missing_explicit_workbench_symbol"
    assert plan["terminal_counts"]["BLOCKED_VALIDATION"] == 1
    assert plan["terminal_counts"]["BLOCKED_MISSING_DATA"] == 0


def test_build_all_lanes_plan_uses_explicit_binding_symbol_for_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workbench.src.run.all_lanes as module

    _write_valid_q001_owner_decision(tmp_path)
    _patch_single_model_plan(
        monkeypatch,
        model_id="CME_MBO_ALPHA",
        lane="cme_futures",
        required_datasets=["mbo_npz"],
    )
    monkeypatch.setattr(
        module,
        "_load_model_event_bindings",
        lambda repo: {"CME_MBO_ALPHA": {"symbol": "MES.v.0"}},
    )

    plan = build_all_lanes_plan(tmp_path, "q001_explicit_symbol")
    row = plan["models"][0]

    assert row["symbol"] == "MES.v.0"
    assert row["execution_eligible"] is True
    assert row["execution_block_reason"] == ""


def test_build_all_lanes_plan_real_bindings_emit_execution_symbols() -> None:
    plan = build_all_lanes_plan(REPO, "fresh_all_lanes_real_symbols")
    rows = {row["model_id"]: row for row in plan["models"]}

    assert rows["SPREAD_BLOWOUT_RECOMPRESSION"]["symbol"] == "MES.v.0"
    assert rows["SPREAD_BLOWOUT_RECOMPRESSION"]["execution_eligible"] is True
    assert rows["NQ_MNQ_LEAD_LAG"]["symbol"] == "MNQ.v.0"
    assert rows["ZN_ZB_ES_NQ_MACRO_IMPULSE"]["symbol"] == "ZN.v.0"
    assert "symbol" not in rows["BOOK_PRESSURE"]
    assert rows["BOOK_PRESSURE"]["execution_eligible"] is False
    assert rows["BOOK_PRESSURE"]["execution_block_reason"] == "missing_explicit_workbench_symbol"
    assert "symbol" not in rows["TREASURY_CTD"]
    assert rows["TREASURY_CTD"]["execution_eligible"] is False
    assert rows["DEALER_HEDGING"]["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert rows["DEALER_HEDGING"]["execution_eligible"] is False
    assert rows["FOPT_ES_CALL"]["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert rows["FOPT_ES_CALL"]["execution_eligible"] is False


def test_build_all_lanes_plan_invalid_q001_available_data_model_is_not_missing_data_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_single_model_plan(
        monkeypatch,
        model_id="CME_AVAILABLE_ALPHA",
        lane="cme_futures",
        required_datasets=["mbo_npz"],
    )

    plan = build_all_lanes_plan(tmp_path, "q001_invalid_available_data")
    row = plan["models"][0]

    assert row["terminal_state"] == "BLOCKED_VALIDATION"
    assert row["available_data_policy"] == "VERIFY_Q001_OWNER_DECISION_BEFORE_EXECUTION"
    assert row["q001_policy_warning"] == "q001_owner_decision_missing"
    assert "reason_code" not in row
    assert plan["terminal_counts"]["BLOCKED_VALIDATION"] == 1
    assert plan["terminal_counts"]["BLOCKED_MISSING_DATA"] == 0


@pytest.mark.parametrize(
    ("decision_text", "reason_code"),
    [
        (None, "q001_owner_decision_missing"),
        ("{not json", "q001_owner_decision_invalid_json"),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "question_id": "Q001",
                    "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
                    "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
                    "options_strict_mbo_warning_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
                    "available_data_research_allowed": True,
                    "accepted_evidence": {
                        "missing_or_unavailable_slots": 999,
                        "strict_mbo_gap_count": 507,
                        "strict_mbo_stale_gap_count": 503,
                        "options_warn_checks": ["options-fixing-mbo-coverage"],
                    },
                    "model_gap_policy": {
                        "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                        "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                        "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
                        "must_emit_skip_or_rejection_reasons": True,
                    },
                }
            ),
            "q001_owner_decision_invalid_accepted_evidence",
        ),
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "question_id": "Q001",
                    "status": "ACCEPTED_AVAILABLE_DATA_SCOPE",
                    "mbo_gap_ledger": "ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
                    "options_strict_mbo_warning_ledger": "STALE_WARNING",
                    "available_data_research_allowed": True,
                    "accepted_evidence": {
                        "missing_or_unavailable_slots": 211,
                        "strict_mbo_gap_count": 507,
                        "strict_mbo_stale_gap_count": 503,
                        "options_warn_checks": ["options-fixing-mbo-coverage"],
                    },
                    "model_gap_policy": {
                        "missing_mbo_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                        "strict_options_quote_required_models": "SIDELINE_UNTIL_DATA_FILLED",
                        "available_data_models": "RUN_WITH_EXPLICIT_COVERAGE",
                        "must_emit_skip_or_rejection_reasons": True,
                    },
                }
            ),
            "q001_owner_decision_invalid_options_ledger",
        ),
    ],
)
def test_build_all_lanes_plan_missing_or_invalid_q001_decision_blocks_strict_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision_text: str | None,
    reason_code: str,
) -> None:
    if decision_text is not None:
        path = tmp_path / "docs" / "project" / "q001_owner_decision.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(decision_text, encoding="utf-8")
    _patch_single_model_plan(
        monkeypatch,
        model_id="FOPT_STRICT_QUOTES",
        lane="cme_options",
        required_datasets=["strict_options_quotes"],
        display_name="FOPT strict options quotes",
    )

    plan = build_all_lanes_plan(tmp_path, "q001_fail_closed")
    row = plan["models"][0]

    assert row["terminal_state"] == "BLOCKED_MISSING_DATA"
    assert row["reason_code"] == reason_code
    assert row["missing_data_policy"] == "SIDELINE_UNTIL_Q001_OWNER_DECISION_VALID"
    assert row["skip_or_rejection_required"] is True
    assert plan["model_universe_status"] == "BLOCKING"
    assert plan["model_gap_gates"] == [
        {
            "gate": "q001_owner_decision",
            "status": "BLOCKING",
            "reason": (
                "Strict missing-data models require a valid Q001 owner decision "
                "before available-data scope can proceed."
            ),
            "reason_code": reason_code,
        }
    ]


def test_build_all_lanes_plan_blocks_registered_lane_with_no_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workbench.src.run.all_lanes as module

    monkeypatch.setattr(module, "list_models", lambda: ["CME_ALPHA"])
    monkeypatch.setattr(
        module,
        "build_models_config",
        lambda: {
            "CME_ALPHA": SimpleNamespace(
                kind="hypothesis",
                required_datasets=["mbo_npz"],
                min_history_years=10,
                robustness_window="discovery",
                latency_lane="sub_10ms",
                execution_assumptions="limit_queue",
                parameter_bounds={},
                signal_field="",
                diagnostics_only=False,
                hyp_id=1,
            )
        },
    )
    monkeypatch.setattr(
        module,
        "load_catalog",
        lambda repo: {
            "CME_ALPHA": SimpleNamespace(
                role="alpha",
                display_name="CME alpha",
            )
        },
    )

    class _FakeEnum(str):
        @property
        def value(self) -> str:
            return str(self)

    class _Config:
        def __init__(self, lane: str) -> None:
            self._lane = lane

        def to_dict(self) -> dict:
            return {
                "lane": self._lane,
                "symbols": ["ES.v.0"],
                "event_types": ["CPI"],
            }

    class _Registration:
        def __init__(self, lane: str) -> None:
            self.lane = _FakeEnum(lane)
            self.test_paths = []

        def config_loader(self) -> _Config:
            return _Config(str(self.lane))

    class _FakeRegistry:
        @staticmethod
        def instance():
            return _FakeRegistry()

        def resolve_lane(self, model_id: str) -> _FakeEnum:
            return _FakeEnum("cme_futures")

        def all_registrations(self):
            return [_Registration("cme_futures"), _Registration("cme_options")]

    monkeypatch.setattr(module, "LaneRegistry", _FakeRegistry)
    monkeypatch.setattr(module, "register_all_lanes", lambda: None)

    plan = build_all_lanes_plan(tmp_path, "lane_gap_test")

    assert plan["lane_model_counts"] == {"cme_futures": 1, "cme_options": 0}
    assert plan["model_universe_status"] == "BLOCKING"
    assert plan["lane_coverage_gates"] == [
        {
            "gate": "lane_model_universe",
            "status": "BLOCKING",
            "lane": "cme_options",
            "reason": "Registered lane has no model ids resolved from the Workbench model registry.",
            "model_count": 0,
        }
    ]
