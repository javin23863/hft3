"""Workbench UI import smoke tests."""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest


def test_campaign_panel_imports_defensive_stub() -> None:
    from workbench.src.core.composition import DefensiveStub, ModelComposition
    from workbench.ui import campaign_panel

    assert campaign_panel.DefensiveStub is DefensiveStub
    assert campaign_panel.ModelComposition is ModelComposition


def test_protocol_does_not_reexport_composition_types() -> None:
    from workbench.src.core import protocol

    assert "CatalogEntry" not in protocol.__all__
    assert not hasattr(protocol, "CatalogEntry")


def test_composition_is_canonical_for_catalog_types() -> None:
    from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition

    assert CatalogEntry.__name__ == "CatalogEntry"
    assert DefensiveStub.__name__ == "DefensiveStub"
    assert ModelComposition.__name__ == "ModelComposition"


def test_model_catalog_imports_catalog_entry() -> None:
    from workbench.src.core.composition import CatalogEntry
    from workbench.src.registry.model_catalog import load_catalog

    catalog = load_catalog()
    assert catalog
    assert isinstance(next(iter(catalog.values())), CatalogEntry)


def test_campaign_panel_full_import() -> None:
    import workbench.ui.campaign_panel as campaign_panel

    assert hasattr(campaign_panel, "init_session")
    assert hasattr(campaign_panel, "model_selector_panel")


def test_app_module_imports() -> None:
    import workbench.ui.app as app

    assert hasattr(app, "REPO")
    assert app.REPO.is_dir()


def test_render_catalog_rows_rejects_empty_key_prefix() -> None:
    from workbench.ui.campaign_panel import _catalog_widget_key, _render_catalog_rows

    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _render_catalog_rows(None, [], {}, key_prefix="", symbol="MES.v.0", audit_grade=True)
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _render_catalog_rows(None, [], {}, key_prefix="   ", symbol="MES.v.0", audit_grade=True)
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _catalog_widget_key("  ", "catalog_search")


def test_catalog_tab_key_patterns_do_not_collide() -> None:
    from workbench.ui.campaign_panel import _catalog_widget_key

    model_id = "HYP_5"
    all_keys = {
        _catalog_widget_key("all_catalog", "catalog_search"),
        _catalog_widget_key("all_catalog", "set", "0", model_id),
        _catalog_widget_key("all_catalog", "run", "0", model_id),
    }
    assert len(all_keys) == 3


def test_workbench_ui_has_no_literal_catalog_search_keys() -> None:
    ui_dir = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui"
    forbidden = (
        'key="catalog_search"',
        "key='catalog_search'",
        'key_prefix = "catalog"',
        "key_prefix = 'catalog'",
    )
    for path in ui_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, f"{path.name} must not contain {pattern!r}"


def test_model_selector_panel_uses_unique_catalog_prefixes() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel.model_selector_panel)
    assert 'key_prefix="all_catalog"' in src
    assert 'key_prefix="catalog"' not in src

    catalog_src = inspect.getsource(campaign_panel._render_catalog_rows)
    assert "Set primary" in catalog_src
    assert "Run campaign" in catalog_src


def test_tabs_are_pipeline_monitor_surface() -> None:
    from workbench.ui.workflow_tabs import WORKFLOW_TABS

    assert WORKFLOW_TABS[:4] == [
        "Autonomous Run",
        "Registry & Data",
        "Backtest Evidence",
        "Latency Evidence",
    ]
    assert "Decision & Registry" in WORKFLOW_TABS
    assert WORKFLOW_TABS.index("Live Monitor") > WORKFLOW_TABS.index("Decision & Registry")
    assert WORKFLOW_TABS.index("Live Monitor") < WORKFLOW_TABS.index("Reports & Analyst")
    assert "Model Selector" not in WORKFLOW_TABS


def test_runtime_contract_is_tab_source_of_truth() -> None:
    from workbench.src.run.evidence_snapshot import workbench_run_sources
    from workbench.src.runtime_contract import load_runtime_contract, validate_runtime_contract
    from workbench.ui.workflow_tabs import WORKFLOW_TABS

    contract = load_runtime_contract()
    schema_path = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "schemas" / "runtime_contract.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == contract["schema_version"]
    assert "tab_contract" in schema["$defs"]
    assert validate_runtime_contract(contract) == []
    assert [tab["name"] for tab in contract["tabs"]] == WORKFLOW_TABS
    latency_tab = next(tab for tab in contract["tabs"] if tab["name"] == "Latency Evidence")
    assert "snapshot.latency/rithmic_endpoint" in latency_tab["runtime_state"]
    assert "system.rithmic_endpoint" not in latency_tab["runtime_state"]
    assert contract["run_sources"] == workbench_run_sources()
    assert contract["blocker_policy"]["silent_blockers_allowed"] is False
    assert contract["blocker_policy"]["fake_pass_allowed"] is False
    assert contract["blocker_policy"]["llm_authority"] == "advisory_only"


def test_runtime_contract_rejects_schema_and_policy_drift() -> None:
    from workbench.src.runtime_contract import load_runtime_contract, validate_runtime_contract

    contract = load_runtime_contract()

    cases = [
        ("top_level_extra", lambda payload: payload.update({"surprise": "field"}), "$ has unexpected field: surprise"),
        (
            "missing_endpoint_field",
            lambda payload: payload["backend_endpoints"][0].pop("request_schema"),
            "backend_endpoints[0] missing required field: request_schema",
        ),
        (
            "endpoint_extra",
            lambda payload: payload["backend_endpoints"][0].update({"operator_override": True}),
            "backend_endpoints[0] has unexpected field: operator_override",
        ),
        (
            "missing_tab_field",
            lambda payload: payload["tabs"][0].pop("backend_service"),
            "tabs[0] missing required field: backend_service",
        ),
        (
            "tab_extra",
            lambda payload: payload["tabs"][0].update({"manual_pass_button": "nope"}),
            "tabs[0] has unexpected field: manual_pass_button",
        ),
        (
            "fake_pass_policy_drift",
            lambda payload: payload["blocker_policy"].update({"fake_pass_allowed": True}),
            "blocker_policy.fake_pass_allowed must be False",
        ),
        (
            "llm_policy_drift",
            lambda payload: payload["blocker_policy"].update({"llm_authority": "can_promote"}),
            "blocker_policy.llm_authority must be 'advisory_only'",
        ),
        (
            "duplicate_backend_endpoint",
            lambda payload: payload["backend_endpoints"][1].update({"id": payload["backend_endpoints"][0]["id"]}),
            "backend endpoint ids must be unique",
        ),
        (
            "stale_backend_endpoint_cli",
            lambda payload: payload["backend_endpoints"][0].update({"cli": "python -m workbench stale-command"}),
            "backend_endpoints[0].cli references unknown Workbench CLI subcommand: 'stale-command'",
        ),
        (
            "malformed_backend_endpoint_cli",
            lambda payload: payload["backend_endpoints"][0].update({"cli": "python scripts/workbench.py run"}),
            "backend_endpoints[0].cli must be 'python -m workbench <subcommand>', got "
            "'python scripts/workbench.py run'",
        ),
        (
            "extra_backend_endpoint_cli_tokens",
            lambda payload: payload["backend_endpoints"][0].update({"cli": "python -m workbench run --extra"}),
            "backend_endpoints[0].cli must be 'python -m workbench <subcommand>', got "
            "'python -m workbench run --extra'",
        ),
        (
            "bad_frontend_component",
            lambda payload: payload["tabs"][0].update(
                {"frontend_component": "workbench.ui.evidence_panels.no_such_renderer"}
            ),
            "Autonomous Run.frontend_component 'workbench.ui.evidence_panels.no_such_renderer' missing attribute "
            "'no_such_renderer' on 'workbench.ui.evidence_panels'",
        ),
        (
            "module_only_frontend_component",
            lambda payload: payload["tabs"][0].update({"frontend_component": "workbench.ui.evidence_panels"}),
            "Autonomous Run.frontend_component 'workbench.ui.evidence_panels' must name a module attribute",
        ),
        (
            "bad_action_component",
            lambda payload: payload["tabs"][1].update(
                {"action_components": ["workbench.ui.campaign_panel.no_such_action"]}
            ),
            "Registry & Data.action_components[0] 'workbench.ui.campaign_panel.no_such_action' missing attribute "
            "'no_such_action' on 'workbench.ui.campaign_panel'",
        ),
        (
            "bad_backend_service",
            lambda payload: payload["tabs"][0].update(
                {"backend_service": "workbench.src.run.evidence_snapshot.no_such_service"}
            ),
            "Autonomous Run.backend_service 'workbench.src.run.evidence_snapshot.no_such_service' missing attribute "
            "'no_such_service' on 'workbench.src.run.evidence_snapshot'",
        ),
    ]

    for label, mutate, expected in cases:
        drifted = copy.deepcopy(contract)
        mutate(drifted)
        errors = validate_runtime_contract(drifted)
        assert expected in errors, label

    stale_sources = copy.deepcopy(contract)
    stale_sources["run_sources"] = list(reversed(stale_sources["run_sources"]))
    assert any("run_sources must match workbench_run_sources()" in error for error in validate_runtime_contract(stale_sources))


def test_runtime_contract_components_are_real_and_complete() -> None:
    from workbench.src.runtime_contract import _workbench_cli_subcommands, validate_runtime_contract

    contract_path = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "config" / "runtime_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    workbench_commands = _workbench_cli_subcommands()

    required_fields = {
        "name",
        "purpose",
        "frontend_component",
        "action_components",
        "backend_service",
        "runtime_state",
        "required_config",
        "required_artifacts",
        "required_schema",
        "pipeline_dependency",
        "expected_output",
        "error_behavior",
        "allowed_actions",
    }
    assert validate_runtime_contract(contract) == []
    for tab in contract["tabs"]:
        assert required_fields <= set(tab), tab["name"]
        if tab["allowed_actions"]:
            assert tab["action_components"], tab["name"]
        assert tab["backend_service"].startswith(("workbench.src.", "workbench.ui."))
        assert tab["expected_output"]
        assert tab["error_behavior"]

    for endpoint in contract["backend_endpoints"]:
        command = endpoint["cli"].replace("python -m workbench ", "")
        assert command in workbench_commands
        assert endpoint["request_schema"]
        assert endpoint["response_schema"]
        assert endpoint["error_response"]


def test_browser_smoke_tracks_backend_tabs_without_destructive_clicks() -> None:
    smoke_src = (Path(__file__).resolve().parents[2] / "scripts" / "workbench_browser_smoke.py").read_text(
        encoding="utf-8"
    )

    assert "from workbench.ui.workflow_tabs import WORKFLOW_TABS" in smoke_src
    assert '"Model Selector"' not in smoke_src
    assert '"Optimisation"' not in smoke_src
    assert '"Promote Candidate"' not in smoke_src
    assert "btn.first.click()" not in smoke_src
    assert "workbench_campaign" in smoke_src
    assert "Set primary" in smoke_src


def test_workbench_preflight_checks_llm_console_import_and_current_tabs() -> None:
    preflight_src = (Path(__file__).resolve().parents[2] / "scripts" / "workbench_preflight.py").read_text(
        encoding="utf-8"
    )

    assert "from workbench.ui.analyst_panel import workbench_llm_console" in preflight_src
    assert "from workbench.ui.workflow_tabs import WORKFLOW_TABS" in preflight_src
    assert "WORKFLOW_TABS still contains 'Model Selector'" in preflight_src
    assert "WORKFLOW_TABS missing 'Registry & Data'" in preflight_src


def test_workbench_launcher_recycles_only_existing_workbench_server() -> None:
    launcher_src = (Path(__file__).resolve().parents[2] / "scripts" / "launch_workbench.ps1").read_text(
        encoding="utf-8"
    )

    assert "function Stop-ExistingWorkbenchOnPort" in launcher_src
    assert "$appPath = (Join-Path $RepoRoot 'apps\\workbench\\ui\\app.py').Replace('\\', '/')" in launcher_src
    assert "$AppPath = Join-Path $RepoRoot 'apps\\workbench\\ui\\app.py'" in launcher_src
    assert '$normalized -like "*streamlit run*"' in launcher_src
    assert '$normalized -like "*$appPath*"' in launcher_src
    assert "Sort-Object -Unique" in launcher_src
    assert "foreach ($workbenchPid in $workbenchPids)" in launcher_src
    assert "Stop-Process -Id $workbenchPid -Force -ErrorAction SilentlyContinue" in launcher_src
    assert launcher_src.index("if ($PreflightOnly)") < launcher_src.index("Stop-ExistingWorkbenchOnPort -Port $Port")
    assert "foreach ($pid in" not in launcher_src.lower()
    assert "port $Port is already in use" in launcher_src


def test_graphify_pre_edit_parses_utc_z_stamp_as_utc() -> None:
    pre_edit_src = (Path(__file__).resolve().parents[2] / "scripts" / "graphify_pre_edit.ps1").read_text(
        encoding="utf-8"
    )

    assert "[datetimeoffset]::Parse" in pre_edit_src
    assert "[Globalization.DateTimeStyles]::AssumeUniversal" in pre_edit_src
    assert "[datetime]::Parse($stampJson.timestamp_utc)" not in pre_edit_src


def test_campaign_controls_only_render_for_workbench_campaign_source() -> None:
    app_src = (Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui" / "app.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(app_src)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]

    def guarded_by_campaign_source(node: ast.AST) -> bool:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if isinstance(parent, ast.If):
                test = ast.unparse(parent.test)
                if test == "run_source == 'workbench_campaign'" or test == 'run_source == "workbench_campaign"':
                    return True
            parent = getattr(parent, "parent", None)
        return False

    campaign_control_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "model_selector_panel")
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "subheader"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "Workbench Campaign Controls"
            )
        )
    ]

    assert campaign_control_calls
    assert all(guarded_by_campaign_source(node) for node in campaign_control_calls)
    assert 'if run_source != "workbench_campaign":\n    selected_campaign = ""' in app_src


def test_workbench_llm_console_is_not_campaign_gated() -> None:
    app_src = (Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui" / "app.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(app_src)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]

    console_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "workbench_llm_console"
    ]
    assert console_calls

    for node in console_calls:
        parent = getattr(node, "parent", None)
        while parent is not None:
            if isinstance(parent, ast.If):
                test = ast.unparse(parent.test)
                assert test not in {
                    "run_source == 'workbench_campaign'",
                    'run_source == "workbench_campaign"',
                }
            parent = getattr(parent, "parent", None)


def test_model_selector_uses_backend_catalog_not_hardcoded_starters() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel.model_selector_panel)
    forbidden = (
        "_RECOMMENDED_STARTERS",
        "Quick start",
        "Trial mode",
        "HYP_5",
        "runnable[0]",
        "backfill_catalog.py",
        'repo / "workbench" / "scripts"',
    )
    for pattern in forbidden:
        assert pattern not in src

    assert "_render_backend_status" in src
    assert "_catalog_symbols" in src
    assert '"workbench"' in src
    assert '"download"' in src
    assert "pythonpath_entries" in inspect.getsource(campaign_panel)
    assert "wb_selection_explicit" in src


def test_backend_status_does_not_surface_event_family_rankings() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel._render_backend_status)
    assert "top types" not in src
    assert "most_common" not in src

    dataset_src = inspect.getsource(campaign_panel._render_dataset_panel)
    assert "Contexts:" not in dataset_src
    assert "Event contexts configured" in dataset_src


def test_catalog_symbols_come_from_event_catalog(tmp_path: Path) -> None:
    from workbench.ui.campaign_panel import _catalog_symbols

    config_dir = tmp_path / "packages" / "data_system" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "events.csv").write_text(
        "event_id,event_type,symbols\n"
        'E1,CUSTOM,"FOO.v.0,BAR.v.0"\n'
        'E2,CUSTOM,"ES.v.0,BAZ.v.0"\n',
        encoding="utf-8",
    )

    assert _catalog_symbols(tmp_path) == ["ES.v.0", "BAR.v.0", "BAZ.v.0", "FOO.v.0"]


def test_catalog_symbols_missing_catalog_does_not_fallback_to_fixed_list(tmp_path: Path) -> None:
    from workbench.ui.campaign_panel import _catalog_symbols

    assert _catalog_symbols(tmp_path) == []


def test_workbench_ui_has_no_stale_operator_surface_strings() -> None:
    ui_dir = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui"
    forbidden = (
        "Model Selector",
        "Quick start",
        "Trial mode",
        "_RECOMMENDED_STARTERS",
        "workbench_pipeline_trial.py",
        "backfill_catalog.py",
        "Load preset:",
        "use_container_width",
    )
    for path in ui_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, f"{path.name} must not contain {pattern!r}"


def test_workbench_cli_removes_crypto_smoke_operator_command() -> None:
    cli_src = (Path(__file__).resolve().parents[2] / "apps" / "workbench" / "__main__.py").read_text(
        encoding="utf-8"
    )

    assert "crypto-smoke" not in cli_src
    assert "fresh-start" in cli_src
    assert "all-lanes" in cli_src


def test_autonomous_panel_is_registry_and_status_driven() -> None:
    app_src = (Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "render_crypto_run_controls" not in app_src
    assert "crypto-smoke" not in app_src
    assert "all-lanes --run-id" in app_src


def test_autonomous_panel_does_not_read_legacy_candidate_reports(tmp_path) -> None:
    from workbench.ui import autonomous_panel

    global_report = tmp_path / "research_cards" / "crypto" / "old_candidate" / "smoke_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text(
        '{"candidate_id":"old_candidate","runs":{"with_btc_node":{"oos_ic_baseline_mean":0.1,"n_rows":1,"n_folds":1}}}',
        encoding="utf-8",
    )

    reports = autonomous_panel._latest_crypto_reports(tmp_path)

    assert reports == []
    assert not hasattr(autonomous_panel, "_start_crypto_smoke")


def test_app_tabs_use_shared_run_evidence_snapshot() -> None:
    import workbench.ui.app as app
    from workbench.ui import evidence_panels

    src = inspect.getsource(app)
    assert "load_run_evidence" in src
    assert "workbench_run_sources()" in src
    assert 'st.query_params.get("source"' in src
    assert "render_crypto_run_controls" not in src
    assert "all-lanes --run-id" in src
    assert "render_backtest_evidence(snapshot)" in src
    assert "render_latency_evidence(snapshot)" in src
    assert "render_signal_diagnostics(snapshot)" in src
    assert "render_robustness(snapshot)" in src
    assert "render_decision_registry(snapshot)" in src
    assert hasattr(evidence_panels, "render_registry_data")
    panel_src = inspect.getsource(evidence_panels)
    assert panel_src.count("render_run_header(snapshot)") >= 9
    assert "Bitcoin state packet transport" in panel_src
    assert "Bitcoin edge packet schema" in panel_src
    assert "Top diagnostic P&L" in panel_src
    assert "Diagnostic P&L ranking" in panel_src
    assert "Top research P&L" not in panel_src
    assert "Smoke OOS equity proxy" in panel_src
    assert "Smoke OOS diagnostic rows" in panel_src
    assert "Backtest rows" not in panel_src
    assert "Backtest & P&L Evidence" not in panel_src
    assert "Smoke robustness prerequisites" in panel_src
    assert "Replay robustness remains blocked" in panel_src
    assert "Holdout passes" not in panel_src
    assert "VectorBT Filter" in panel_src
    assert "Adapter invoked" in panel_src
    assert "Filter backend" in panel_src
    assert "Rejection reason" in panel_src
    assert "Safe OOS Signal Source" in panel_src
    assert "Signal Adapter Rejection" in panel_src
    assert "Required for adapter acceptance" in panel_src
    assert "Existing repo sources for this boundary" in panel_src
    assert "Crypto Execution Replay" in panel_src
    assert "Only L3_VALIDATED or FULL_EXECUTION rows satisfy" in panel_src
    assert "No crypto execution replay artifact is attached to this selected crypto run." in panel_src
    assert "HFT Replay Validation" not in panel_src
    assert "Leakage controls" in panel_src
    assert "Smoke passes" in panel_src
    assert "Self-Learning Loop" in panel_src
    assert "Parameter learning is controlled by config bounds" in panel_src
    assert "Analyst can promote" in panel_src
    assert "After-Action Packet" in panel_src
    assert "AlphaGeometry/OpenFoundry Relationship Review" in panel_src
    assert "Failed Required Checks" in panel_src
    assert "Smoke pass is only a prerequisite" not in panel_src
    assert "Provider Status" in panel_src
    assert "Rithmic Endpoint Status" in panel_src
    assert "Cross-Lane Feature Fabric" in panel_src
    assert "Registered model lanes" in panel_src
    assert "Paper/Chicago API parameters are missing" in panel_src
    assert "Google/Gemini" not in panel_src
    assert "Evidence candidate" in panel_src
    assert "Smoke triage order" in panel_src
    assert "Positive P&L diagnostics" in panel_src
    assert "Research passes" not in panel_src
    assert "Top diagnostic candidate" not in panel_src
    assert "Candidate ranking" not in panel_src
    assert "research_pass_count" not in panel_src
    assert "crypto-smoke" not in src


def test_wallet_panel_is_guarded_operator_surface() -> None:
    from workbench.ui.workflow_tabs import WORKFLOW_TABS
    from workbench.ui import wallet_panel
    from workbench.src.run import wallet_ops

    assert "Wallet" in WORKFLOW_TABS
    panel_src = inspect.getsource(wallet_panel)
    ops_src = inspect.getsource(wallet_ops)
    assert "Operational hot wallet only" in panel_src
    assert "Refresh Wallet" in panel_src
    assert "Refresh Activity" in panel_src
    assert "Preview Transaction" in panel_src
    assert "Broadcast BTC" in panel_src
    assert "Scan to receive BTC" in panel_src
    assert "Wallet passphrase" in panel_src
    assert "There is no Workbench amount cap" in panel_src
    assert "approved small operational transfer" not in panel_src
    assert "MAX_UI_SEND_BTC" not in panel_src
    assert "MAX_UI_SEND_BTC" not in ops_src
    assert "walletlock" in ops_src
    assert "sendtoaddress" in ops_src
    assert "walletcreatefundedpsbt" in ops_src
    assert "stdin=passphrase" in ops_src


def test_verify_command_does_not_overclaim_full_pipeline_readiness() -> None:
    repo = Path(__file__).resolve().parents[2]
    main_src = (repo / "apps" / "workbench" / "__main__.py").read_text(encoding="utf-8")
    verify_src = (repo / "apps" / "workbench" / "src" / "verify.py").read_text(encoding="utf-8")

    assert "Full readiness gate" not in main_src
    assert "Full readiness gate" not in verify_src
    assert "Workbench preflight check" in verify_src


def test_crypto_smoke_cli_is_not_a_production_workbench_command() -> None:
    repo = Path(__file__).resolve().parents[2]
    main_src = (repo / "apps" / "workbench" / "__main__.py").read_text(encoding="utf-8")

    assert "crypto-smoke" not in main_src
    assert 'result.get("state") == "completed"' not in main_src
    assert '{"completed", "blocked"}' not in main_src


def test_crypto_execution_replay_ui_does_not_make_l2_gate_equivalent() -> None:
    repo = Path(__file__).resolve().parents[2]
    panel_src = (repo / "apps" / "workbench" / "ui" / "evidence_panels.py").read_text(encoding="utf-8")

    assert "Crypto Execution Replay (L2/L3)" not in panel_src
    assert "Observed L2/L3 crypto execution-replay evidence" not in panel_src
    assert "Only L3_VALIDATED or FULL_EXECUTION rows satisfy" in panel_src
