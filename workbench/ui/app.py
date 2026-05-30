"""Streamlit microstructure workbench UI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pandas as pd
import streamlit as st

st.set_page_config(page_title="HFT3 Workbench", layout="wide")

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
from workbench.ui.flow_state import campaign_progress_panel, resolve_period_event  # noqa: E402

init_session(REPO)

with st.sidebar:
    personal_lock_sidebar(REPO)
    st.caption("Grader checklist: `docs/workbench/GRADER_CHECKLIST.md`")
    st.caption("Campaign CLI: `python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run`")
    st.caption("Verify: `powershell -File scripts/verify_workbench.ps1`")

st.title("Microstructure Backtesting Workbench")

_active = st.session_state.get("wb_active_campaign", "")
if _active:
    campaign_progress_panel(REPO, _active)

tab_names = [
    "Model Selector",
    "Personal Runs",
    "Backtest Results",
    "Latency Viability",
    "Signal Diagnostics",
    "Robustness",
    "Optimisation",
    "Report",
    "Analyst",
]
tabs = st.tabs(tab_names)

runs_dir = REPO / "research_cards" / "workbench_runs"
run_dirs = sorted(runs_dir.glob("*"), reverse=True) if runs_dir.is_dir() else []
run_labels = [d.name for d in run_dirs]

selected_model = ""
selected_symbol = "MES.v.0"
selected_campaign = ""

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
                st.selectbox(
                    "Campaign period",
                    [""] + periods,
                    key="wb__period_sel",
                )
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
    st.header("Personal Runs")
    personal_runs_panel(REPO, selected_model, selected_symbol)

with tabs[2]:
    st.header("Backtest Results")
    if diag_data:
        rep = diag_data if "net_pnl" in diag_data else {}
        if "event_results" in diag_data:
            st.dataframe(pd.DataFrame(diag_data["event_results"]), use_container_width=True)
        if rep:
            st.metric("Net PnL", rep.get("net_pnl", 0))
            st.metric("Trades", rep.get("num_trades", 0))
            pnl_lat = rep.get("pnl_by_latency", {})
            if pnl_lat:
                st.line_chart(pd.Series(pnl_lat))
    elif selected_campaign:
        st.info("Campaign running or waiting for first event diagnostics.")
    else:
        st.info("Click **Select & run campaign** on a model in Model Selector.")

with tabs[3]:
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
        st.info("Results appear when the first event completes.")
    else:
        st.info("Start a campaign from Model Selector.")

with tabs[4]:
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

with tabs[5]:
    st.header("Robustness")
    wfc_summary_path = (
        runs_dir / selected_campaign / "wfc" / "wfc_summary.json"
        if selected_campaign
        else None
    )
    campaign_summary_path = runs_dir / selected_campaign / "summary.json" if selected_campaign else None
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
        if wfc_data.get("n_folds"):
            st.caption(f"Folds: {wfc_data.get('n_folds')} | Params: {wfc_data.get('n_parameter_combinations', '—')}")
        if wfc_data.get("top_decile_oos_median") is not None:
            st.caption(
                f"Top decile OOS median: {wfc_data.get('top_decile_oos_median', 0):.3f} | "
                f"Bottom: {wfc_data.get('bottom_decile_oos_median', 0):.3f}"
            )
        if wfc_data.get("fold_correlations"):
            with st.expander("Per-fold correlations"):
                st.json(wfc_data["fold_correlations"])
        if wfc_data.get("rejection_reasons"):
            st.warning("WFC rejection: " + "; ".join(wfc_data["rejection_reasons"]))
        scatter = runs_dir / selected_campaign / "wfc" / "is_vs_oos_scatter.png"
        if scatter.is_file():
            st.image(str(scatter), caption="IS vs OOS parameter matrix")
        with st.expander("WFC detail"):
            st.json(wfc_data)

    if diag_data:
        st.write(f"Robustness passed: **{diag_data.get('robustness_passed', 'n/a')}**")
        st.write(f"Over-fit risk: **{diag_data.get('overfit_risk', 'n/a')}**")
        if "periods" in diag_data:
            st.json(diag_data.get("periods"))
    elif selected_campaign and not wfc_data:
        st.info("Results appear when the first event completes.")
    elif not selected_campaign:
        st.info("Start a campaign from Model Selector.")

with tabs[6]:
    st.header("Optimisation")
    st.caption("Run a campaign first — latency/cost sweeps attach to campaign events.")
    promote = False
    if campaign_summary_path and campaign_summary_path.is_file():
        promote = bool(json.loads(campaign_summary_path.read_text(encoding="utf-8")).get("promote_candidate"))
    elif diag_data:
        promote = bool(diag_data.get("promote_candidate"))
    st.button("Promote Candidate", disabled=not promote, key="wb__promote_candidate")

with tabs[7]:
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
            st.subheader("C++ viability report")
            st.markdown(md_path.read_text(encoding="utf-8"))

        aar_path = art / "after_action_report.md"
        sym_path = art / "after_action_symbolic.json"
        meta_path = art / "after_action_meta.json"
        diag_path = art / "diagnostics.json"

        if meta_path.is_file() or sym_path.is_file() or aar_path.is_file():
            st.subheader("After-action summary")
            diag_local = json.loads(diag_path.read_text(encoding="utf-8")) if diag_path.is_file() else {}
            sym_data = json.loads(sym_path.read_text(encoding="utf-8")) if sym_path.is_file() else {}
            meta_data = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Lane pass", str(diag_local.get("lane_pass", "n/a")))
            c2.metric("Break-even (µs)", f"{diag_local.get('breakeven_us', 0):.0f}")
            c3.metric("Symbolic", "PASS" if sym_data.get("passed") else "FAIL")
            if sym_data.get("violations"):
                c4.caption(f"Violations: {len(sym_data['violations'])}")

            if meta_data.get("skip_reasons"):
                st.warning(f"After-action skipped/partial: {', '.join(meta_data['skip_reasons'])}")

            if aar_path.is_file():
                st.markdown(aar_path.read_text(encoding="utf-8"))
            elif meta_data.get("llm_status"):
                st.info(f"No LLM report ({meta_data.get('llm_status')}). See **Analyst** tab.")

            with st.expander("After-action artifacts"):
                for name in (
                    "after_action_packet.json",
                    "after_action_symbolic.json",
                    "after_action_meta.json",
                    "kg_slice.json",
                ):
                    p = art / name
                    if p.is_file():
                        st.caption(name)
                        st.json(json.loads(p.read_text(encoding="utf-8")))
    elif selected_campaign:
        summary_path = runs_dir / selected_campaign / "summary.json"
        if summary_path.is_file():
            st.json(json.loads(summary_path.read_text(encoding="utf-8")))
        else:
            st.info("Campaign in progress — reports appear per event.")
    else:
        st.info("Click **Select & run campaign** on a model in Model Selector.")

with tabs[8]:
    st.header("Analyst")
    analyst_panel(REPO, selected_campaign, period_choice, event_choice)
