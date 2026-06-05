"""Streamlit microstructure workbench UI."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import streamlit as st

from workbench.src.artifacts.paths import workbench_runs_dir

st.set_page_config(page_title="HFT3 Workbench", layout="wide")

from workbench.ui.analyst_panel import analyst_panel, workbench_llm_console  # noqa: E402
from workbench.ui.campaign_panel import (  # noqa: E402
    campaign_events,
    campaign_periods,
    init_session,
    model_selector_panel,
    personal_lock_sidebar,
    personal_runs_panel,
)
from workbench.ui.workflow_tabs import WORKFLOW_TAB_CONTRACTS, WORKFLOW_TABS  # noqa: E402
from workbench.ui.wallet_panel import render_wallet_panel  # noqa: E402
from workbench.ui.flow_state import campaign_progress_panel, resolve_period_event  # noqa: E402
from workbench.src.run.evidence_snapshot import default_source, load_run_evidence, workbench_run_sources  # noqa: E402
from workbench.ui.evidence_panels import (  # noqa: E402
    render_autonomous_run,
    render_backtest_evidence,
    render_decision_registry,
    render_latency_evidence,
    render_live_monitor,
    render_registry_data,
    render_reports_analyst,
    render_robustness,
    render_signal_diagnostics,
    render_system,
)

init_session(REPO)

with st.sidebar:
    personal_lock_sidebar(REPO)
    run_sources = workbench_run_sources()
    query_source = str(st.query_params.get("source", ""))
    if query_source in run_sources:
        st.session_state.wb_run_source = query_source
    if "wb_run_source" not in st.session_state:
        st.session_state.wb_run_source = default_source(REPO)
    st.selectbox(
        "Run source",
        run_sources,
        key="wb_run_source",
    )
    st.caption("Campaign CLI: `python -m workbench campaign`")
    st.caption("All lanes CLI: `python -m workbench all-lanes --run-id <active_run_id>`")
    st.caption("Verify: `powershell -File scripts/verify_workbench.ps1`")

st.title("Microstructure Backtesting Workbench")

run_source = st.session_state.get("wb_run_source", "")
_active = st.session_state.get("wb_active_campaign", "") if run_source == "workbench_campaign" else ""
if _active:
    campaign_progress_panel(REPO, _active)

if st.session_state.get("wb_nav_hint"):
    st.info(st.session_state.wb_nav_hint)

tabs = st.tabs(WORKFLOW_TABS)
tab_views = dict(zip(WORKFLOW_TABS, tabs, strict=True))

runs_dir = workbench_runs_dir()
run_dirs = sorted(runs_dir.glob("*"), reverse=True) if runs_dir.is_dir() else []
run_labels = [d.name for d in run_dirs]

selected_model = st.session_state.get("wb_selected_model", "")
selected_symbol = st.session_state.get("wb_symbol", "MES.v.0")
selected_campaign = st.session_state.get("wb_active_campaign", "")

with tab_views["Registry & Data"]:
    if run_source == "workbench_campaign":
        st.subheader("Workbench Campaign Controls")
        selected_model, selected_symbol, selected_campaign = model_selector_panel(REPO)
        with st.expander("Recent campaign artifacts"):
            legacy_run = st.selectbox("Load campaign", [""] + run_labels, key="wb__legacy_run")
            if legacy_run and not selected_campaign:
                selected_campaign = legacy_run
                st.session_state.wb_active_campaign = legacy_run
                st.session_state.wb__period_sel = ""
                st.session_state.wb__event_sel = ""
                st.session_state.wb_auto_period = ""
                st.session_state.wb_auto_event = ""
        if selected_campaign:
            periods = campaign_periods(REPO, selected_campaign)
            with st.expander("Drill-down period / event"):
                if periods:
                    st.selectbox("Campaign period", [""] + periods, key="wb__period_sel")
                    pc = st.session_state.get("wb__period_sel") or st.session_state.get("wb_auto_period", "")
                    if pc:
                        events = campaign_events(REPO, selected_campaign, pc)
                        st.selectbox("Event in period", [""] + events, key="wb__event_sel")

if run_source != "workbench_campaign":
    selected_campaign = ""
elif not selected_campaign:
    selected_campaign = st.session_state.get("wb_active_campaign", "")

period_choice, event_choice = resolve_period_event(REPO, selected_campaign)
snapshot = load_run_evidence(REPO, run_source, campaign_id=selected_campaign)

contract_renderers = {
    "workbench.ui.evidence_panels.render_autonomous_run": lambda: render_autonomous_run(snapshot),
    "workbench.ui.evidence_panels.render_registry_data": lambda: render_registry_data(snapshot),
    "workbench.ui.evidence_panels.render_backtest_evidence": lambda: render_backtest_evidence(snapshot),
    "workbench.ui.evidence_panels.render_latency_evidence": lambda: render_latency_evidence(snapshot),
    "workbench.ui.evidence_panels.render_signal_diagnostics": lambda: render_signal_diagnostics(snapshot),
    "workbench.ui.evidence_panels.render_robustness": lambda: render_robustness(snapshot),
    "workbench.ui.evidence_panels.render_decision_registry": lambda: render_decision_registry(snapshot),
    "workbench.ui.evidence_panels.render_live_monitor": lambda: render_live_monitor(snapshot),
    "workbench.ui.evidence_panels.render_reports_analyst": lambda: render_reports_analyst(snapshot),
    "workbench.ui.wallet_panel.render_wallet_panel": render_wallet_panel,
    "workbench.ui.evidence_panels.render_system": lambda: render_system(snapshot, REPO),
    "workbench.ui.campaign_panel.personal_runs_panel": lambda: personal_runs_panel(REPO, selected_model, selected_symbol),
}
contract_action_renderers = {
    ("Registry & Data", "workbench.ui.campaign_panel.model_selector_panel"): "pre-evidence controls",
    ("Reports & Analyst", "workbench.ui.analyst_panel.workbench_llm_console"): "advisory console",
    ("Reports & Analyst", "workbench.ui.analyst_panel.analyst_panel"): "campaign drill-down",
    ("Wallet", "workbench.ui.wallet_panel.render_wallet_panel"): "primary renderer",
    ("Personal Runs", "workbench.ui.campaign_panel.personal_runs_panel"): "primary renderer",
}

missing_renderers = [
    tab_contract["frontend_component"]
    for tab_contract in WORKFLOW_TAB_CONTRACTS
    if tab_contract["frontend_component"] not in contract_renderers
]
if missing_renderers:
    raise RuntimeError(f"Workbench tab contract has no app renderer for: {missing_renderers}")
missing_action_renderers = sorted(
    {
        (str(tab_contract["name"]), action_component)
        for tab_contract in WORKFLOW_TAB_CONTRACTS
        for action_component in tab_contract["action_components"]
        if (str(tab_contract["name"]), action_component) not in contract_action_renderers
    }
)
if missing_action_renderers:
    raise RuntimeError(f"Workbench tab contract has no app action renderer for: {missing_action_renderers}")

for tab_contract in WORKFLOW_TAB_CONTRACTS:
    tab_name = str(tab_contract["name"])
    component = str(tab_contract["frontend_component"])
    with tab_views[tab_name]:
        if tab_name == "Registry & Data":
            st.divider()
        if tab_name == "Personal Runs":
            st.header("Personal Runs")
        contract_renderers[component]()
        if tab_name == "Reports & Analyst":
            st.divider()
            workbench_llm_console(snapshot)
            if run_source == "workbench_campaign":
                st.divider()
                analyst_panel(REPO, selected_campaign, period_choice, event_choice)
