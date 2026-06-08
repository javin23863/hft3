"""Streamlit microstructure workbench UI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import streamlit as st

from workbench.src.artifacts.paths import runtime_validation_dir, workbench_runs_dir, artifact_root

# Guard st.set_page_config so it only runs in Streamlit context (not during pytest imports)
# Catch only StreamlitAPIException which is raised when not in Streamlit context
try:
    st.set_page_config(page_title="HFT3 Workbench", layout="wide")
except st.errors.StreamlitAPIException:
    pass  # Not in Streamlit context (e.g., pytest import)

def _extract_aggregate_from_periods(diag: dict) -> dict:
    """Extract net_pnl + num_trades from summary-level periods list."""
    periods = diag.get("periods", [])
    if not periods or not isinstance(periods, list):
        return {}
    total_pnl = 0.0
    total_trades = 0
    for p in periods:
        total_pnl += float(p.get("net_pnl", 0))
        total_trades += int(p.get("num_trades", 0))
    return {"net_pnl": total_pnl, "num_trades": total_trades}

from workbench.ui.analyst_panel import analyst_panel  # noqa: E402
from workbench.ui.campaign_panel import (  # noqa: E402
    campaign_events,
    campaign_periods,
    init_session,
    load_campaign_diagnostics,
    model_selector_panel,
    personal_lock_sidebar,
    personal_runs_panel,
)
from workbench.ui.workflow_tabs import WORKFLOW_TABS  # noqa: E402
from workbench.ui.flow_state import campaign_progress_panel, navigate_to_tab, resolve_period_event  # noqa: E402

init_session(REPO)

with st.sidebar:
    personal_lock_sidebar(REPO)
    st.caption("Grader checklist: `docs/workbench/GRADER_CHECKLIST.md`")
    st.caption("Trial CLI: `python scripts/workbench_pipeline_trial.py`")
    st.caption("Verify: `powershell -File scripts/verify_workbench.ps1`")

st.title("Microstructure Backtesting Workbench")

_active = st.session_state.get("wb_active_campaign", "")
if _active:
    campaign_progress_panel(REPO, _active)

if st.session_state.get("wb_nav_hint"):
    st.info(st.session_state.wb_nav_hint)

tabs = st.tabs(WORKFLOW_TABS)

runs_dir = workbench_runs_dir()
run_dirs = sorted(
    [d for d in runs_dir.glob("*") if d.is_dir() and (
        (d / "status.json").is_file() or (d / "summary.json").is_file() or (d / "diagnostics.json").is_file()
    )],
    reverse=True,
) if runs_dir.is_dir() else []
run_labels = [d.name for d in run_dirs]

selected_model = st.session_state.get("wb_selected_model", "")
selected_symbol = st.session_state.get("wb_symbol", "MES.v.0")
selected_campaign = st.session_state.get("wb_active_campaign", "")

with tabs[0]:
    st.header("Model Selector")
    selected_model, selected_symbol, selected_campaign = model_selector_panel(REPO)
    with st.expander("Advanced — legacy single run"):
        legacy_run = st.selectbox("Legacy single run", [""] + run_labels, key="wb__legacy_run")
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

if not selected_campaign:
    selected_campaign = st.session_state.get("wb_active_campaign", "")

period_choice, event_choice = resolve_period_event(REPO, selected_campaign)
campaign_summary_path = runs_dir / selected_campaign / "summary.json" if selected_campaign else None

diag_data = load_campaign_diagnostics(REPO, selected_campaign, period_choice, event_choice)
if diag_data is None and selected_campaign:
    diag_data = load_campaign_diagnostics(REPO, selected_campaign, period_choice)
if diag_data is None and selected_campaign:
    diag_data = load_campaign_diagnostics(REPO, selected_campaign)

with tabs[1]:
    st.header("Backtest Results")
    if not selected_model:
        st.info("Choose a model on **Model Selector**, then **Set primary** or **Run campaign**.")
    elif diag_data:
        rep = diag_data if "net_pnl" in diag_data else _extract_aggregate_from_periods(diag_data)
        if "event_results" in diag_data:
            st.dataframe(pd.DataFrame(diag_data["event_results"]), width="stretch")
        if rep:
            st.metric("Net PnL", rep.get("net_pnl", 0))
            st.metric("Trades", rep.get("num_trades", 0))
            pnl_lat = rep.get("pnl_by_latency", {})
            if pnl_lat:
                st.line_chart(pd.Series(pnl_lat))
    elif selected_campaign:
        _campaign_base = runs_dir / selected_campaign
        _has_status = (_campaign_base / "status.json").is_file()
        _has_summary = (_campaign_base / "summary.json").is_file()
        if not _has_status and not _has_summary:
            st.warning("Campaign directory exists but produced no results — likely crashed or was stopped before any events ran.")
        else:
            st.info("Campaign running — open this tab as events complete (~5 min per event on Windows).")
    else:
        st.info("Click **Run campaign** on Model Selector (trial mode: available NPZ only).")

with tabs[2]:
    st.header("Latency Viability")
    if diag_data and "breakeven_us" in diag_data:
        c1, c2, c3 = st.columns(3)
        c1.metric("Break-even (µs)", f"{diag_data.get('breakeven_us', 0):.0f}")
        c2.metric("C++ p99 (µs)", f"{diag_data.get('measured_production_p99_us', 0):.0f}")
        c3.metric("Buffer (µs)", f"{diag_data.get('latency_profitability_buffer_us', 0):.0f}")
        st.caption(f"Survives C++ delay: {diag_data.get('survives_cpp_execution_delay')}")
    elif diag_data:
        st.write(f"Period gate pass: **{diag_data.get('gate_pass')}** | survives_cpp: **{diag_data.get('survives_cpp')}**")
    elif selected_campaign:
        st.info("Latency metrics appear when the first event completes.")
    else:
        st.info("Run a campaign from Model Selector.")

with tabs[3]:
    st.header("Signal Diagnostics")
    if diag_data and diag_data.get("composition"):
        st.subheader("Campaign composition")
        st.json(diag_data.get("composition"))
    if selected_campaign and period_choice and event_choice:
        trace_path = (
            runs_dir
            / selected_campaign
            / "periods"
            / period_choice.replace(" ", "_")
            / "events"
            / event_choice
            / "composition_trace.json"
        )
        if trace_path.is_file():
            st.subheader("Composition trace (event)")
            st.json(json.loads(trace_path.read_text(encoding="utf-8")))
    st.caption("PDF OFI/VPIN and HYP signal histograms from run artifacts")

with tabs[4]:
    st.header("Robustness")
    wfc_summary_path = runs_dir / selected_campaign / "wfc" / "wfc_summary.json" if selected_campaign else None
    wfc_data = {}
    if wfc_summary_path and wfc_summary_path.is_file():
        wfc_data = json.loads(wfc_summary_path.read_text(encoding="utf-8"))
    elif campaign_summary_path and campaign_summary_path.is_file():
        wfc_data = json.loads(campaign_summary_path.read_text(encoding="utf-8")).get("wfc", {})

    if wfc_data:
        st.subheader("Walk Forward Correlation (WFC)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("WFC status", wfc_data.get("wfc_status", "—"))
        c2.metric("Pearson", f"{wfc_data.get('pearson', 0):.3f}")
        c3.metric("Spearman", f"{wfc_data.get('spearman', 0):.3f}")
        c4.metric("Fold consistency", f"{wfc_data.get('positive_fold_ratio', 0):.0%}")
        if wfc_data.get("fold_correlations"):
            with st.expander("Per-fold correlations"):
                st.json(wfc_data["fold_correlations"])
        if wfc_data.get("rejection_reasons"):
            st.warning("WFC rejection: " + "; ".join(wfc_data["rejection_reasons"]))
        scatter = runs_dir / selected_campaign / "wfc" / "is_vs_oos_scatter.png"
        if scatter.is_file():
            st.image(str(scatter), caption="IS vs OOS parameter matrix")
    if diag_data:
        st.write(f"Robustness passed: **{diag_data.get('robustness_passed', 'n/a')}**")
    elif not selected_campaign:
        st.info("Run a campaign from Model Selector.")

with tabs[5]:
    st.header("Optimisation")
    promote = False
    if campaign_summary_path and campaign_summary_path.is_file():
        promote = bool(json.loads(campaign_summary_path.read_text(encoding="utf-8")).get("promote_candidate"))
    elif diag_data:
        promote = bool(diag_data.get("promote_candidate"))
    st.button("Promote Candidate", disabled=not promote, key="wb__promote_candidate")

with tabs[6]:
    st.header("Report")

    def _artifact_base() -> Path | None:
        if not selected_campaign:
            return None
        if period_choice and event_choice:
            return (
                runs_dir
                / selected_campaign
                / "periods"
                / period_choice.replace(" ", "_")
                / "events"
                / event_choice
            )
        legacy = runs_dir / selected_campaign
        if legacy.is_dir() and (legacy / "diagnostics.json").is_file():
            return legacy
        return None

    art = _artifact_base()
    if art:
        md_path = art / "report.md"
        if md_path.is_file():
            st.markdown(md_path.read_text(encoding="utf-8"))
    elif selected_campaign:
        st.info("Campaign in progress — reports appear per event.")
    else:
        st.info("Run a campaign from Model Selector.")

with tabs[7]:
    st.header("Analyst")
    analyst_panel(REPO, selected_campaign, period_choice, event_choice)

with tabs[8]:
    st.header("System — certification & runtime")
    vdir = runtime_validation_dir()
    if vdir.is_dir():
        for name in (
            "certification_registry.json",
            "fast_gate_report.json",
            "backtester_certification_scorecard.json",
            "champion_promotion_gate_report.json",
        ):
            fp = vdir / name
            if fp.is_file():
                with st.expander(name):
                    st.json(json.loads(fp.read_text(encoding="utf-8")))
    else:
        st.info("No runtime/validation artifacts yet.")
    legacy_hyp = artifact_root() / "all_hypotheses.json"
    if legacy_hyp.is_file():
        with st.expander("Legacy — all_hypotheses.json"):
            st.json(json.loads(legacy_hyp.read_text(encoding="utf-8")))

with tabs[9]:
    st.header("Personal Runs")
    personal_runs_panel(REPO, selected_model, selected_symbol)

with tabs[10]:
    st.header("Equities")
    from workbench.ui.equities_panel import equities_panel as _equities_panel
    _equities_panel(REPO)

with tabs[11]:
    from workbench.ui.autonomous_panel import autonomous_panel as _autonomous_panel
    _autonomous_panel(REPO)
