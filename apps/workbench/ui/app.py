"""Streamlit microstructure workbench UI."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import streamlit as st

from workbench.src.artifacts.paths import workbench_runs_dir

st.set_page_config(page_title="HFT3 Workbench", layout="wide")

from workbench.ui.analyst_panel import analyst_panel  # noqa: E402
from workbench.ui.autonomous_panel import render_crypto_run_controls  # noqa: E402
from workbench.ui.campaign_panel import (  # noqa: E402
    campaign_events,
    campaign_periods,
    init_session,
    model_selector_panel,
    personal_lock_sidebar,
    personal_runs_panel,
)
from workbench.ui.workflow_tabs import WORKFLOW_TABS  # noqa: E402
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
    st.caption("Crypto CLI: `python -m workbench crypto-smoke`")
    st.caption("Verify: `powershell -File scripts/verify_workbench.ps1`")

st.title("Microstructure Backtesting Workbench")

run_source = st.session_state.get("wb_run_source", "")
_active = st.session_state.get("wb_active_campaign", "") if run_source == "workbench_campaign" else ""
if _active:
    campaign_progress_panel(REPO, _active)

if st.session_state.get("wb_nav_hint"):
    st.info(st.session_state.wb_nav_hint)

tabs = st.tabs(WORKFLOW_TABS)

runs_dir = workbench_runs_dir()
run_dirs = sorted(runs_dir.glob("*"), reverse=True) if runs_dir.is_dir() else []
run_labels = [d.name for d in run_dirs]

selected_model = st.session_state.get("wb_selected_model", "")
selected_symbol = st.session_state.get("wb_symbol", "MES.v.0")
selected_campaign = st.session_state.get("wb_active_campaign", "")

with tabs[0]:
    if run_source == "crypto_lane":
        render_crypto_run_controls(REPO)
        st.divider()

with tabs[1]:
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

with tabs[0]:
    render_autonomous_run(snapshot)

with tabs[1]:
    st.divider()
    render_registry_data(snapshot)

with tabs[2]:
    render_backtest_evidence(snapshot)

with tabs[3]:
    render_latency_evidence(snapshot)

with tabs[4]:
    render_signal_diagnostics(snapshot)

with tabs[5]:
    render_robustness(snapshot)

with tabs[6]:
    render_decision_registry(snapshot)

with tabs[7]:
    render_live_monitor(snapshot)

with tabs[8]:
    render_reports_analyst(snapshot)
    if run_source == "workbench_campaign":
        st.divider()
        analyst_panel(REPO, selected_campaign, period_choice, event_choice)

with tabs[9]:
    render_wallet_panel()

with tabs[10]:
    render_system(snapshot, REPO)

with tabs[11]:
    st.header("Personal Runs")
    personal_runs_panel(REPO, selected_model, selected_symbol)
