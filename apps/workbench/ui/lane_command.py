"""Lane Command Center: lane-first workbench landing and detail views.

All rendering consumes WorkbenchTruth. No independent state assembly.
Each lane is a first-class card. Clicking drills down to lane detail.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import streamlit as st

from workbench.src.state.workbench_truth import (
    WorkbenchTruth,
    LaneTruth,
    CmeEntryTruth,
    EquitiesEntryTruth,
    OptionsEntryTruth,
    CryptoEntryTruth,
    build_workbench_truth,
)


# ---------------------------------------------------------------------------
# Lane Command Center — main landing
# ---------------------------------------------------------------------------


def _lane_status_badge(status: str) -> str:
    if status == "operational":
        return ":green[OPERATIONAL]"
    if status == "degraded":
        return ":orange[DEGRADED]"
    if status in ("blocked", "incomplete"):
        return ":red[BLOCKED]"
    return f":grey[{status.upper()}]"


def render_lane_command_center(repo: Path) -> None:
    truth = build_workbench_truth(repo)

    # ---- Header strip ----
    st.title("Lane Command Center")
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.caption(f"Repo: `{truth.repo_root}`")
    c2.caption(f"Commit: `{truth.repo_commit[:12]}`")
    c3.caption(f"Generated: `{truth.generated_at[:19]}`")

    # ---- Lane cards ----
    st.subheader("Lanes")
    cols = st.columns(len(truth.lanes))
    for idx, lane in enumerate(truth.lanes):
        with cols[idx]:
            _render_lane_card(lane, repo)

    # ---- Advanced section ----
    with st.expander("Advanced — legacy tabs & raw artifacts", expanded=False):
        from workbench.ui.campaign_panel import model_selector_panel
        from workbench.ui.autonomous_panel import autonomous_panel
        from workbench.ui.equities_panel import equities_panel as _eq_panel

        adv_tab = st.radio(
            "Legacy panels",
            ["CME Model Selector (legacy)", "Autonomous Campaign", "Equities Backtest (legacy)"],
            horizontal=True,
        )
        if adv_tab == "CME Model Selector (legacy)":
            model_selector_panel(repo)
        elif adv_tab == "Autonomous Campaign":
            autonomous_panel(repo)
        else:
            _eq_panel(repo)


def _render_lane_card(lane: LaneTruth, repo: Path) -> None:
    """Single lane card in the command center."""
    with st.container(border=True):
        st.markdown(f"### {lane.lane_name}")
        st.caption(lane.description)
        st.markdown(_lane_status_badge(lane.status))

        c1, c2 = st.columns(2)
        c1.metric("Entries", lane.universe_size)
        c2.metric("Data ready", f"{lane.data_readiness_pct:.0f}%")

        c3, c4 = st.columns(2)
        c3.metric("Available", lane.sessions_available)
        c4.metric("Blocked", lane.sessions_blocked)

        c5, c6 = st.columns(2)
        c5.metric("Models bound", lane.models_bound)
        c6.metric("Active runs", lane.active_runs)

        if lane.champions or lane.candidates:
            c7, c8, c9 = st.columns(3)
            c7.metric("Champions", lane.champions)
            c8.metric("Candidates", lane.candidates)
            c9.metric("Rejected", lane.rejected)

        if lane.primary_blockers:
            with st.expander(f"Blockers ({len(lane.primary_blockers)})", expanded=False):
                for b in lane.primary_blockers[:10]:
                    st.error(b)

        st.caption(f"**Next:** {lane.next_action}")

        # Drill-down button
        lane_key = f"wb__lane_detail_{lane.lane_id}"
        if st.button("Open", key=lane_key, use_container_width=True):
            st.session_state["wb_lane_detail"] = lane.lane_id
            st.rerun()


def render_lane_detail(repo: Path) -> None:
    """Render the detail view for a selected lane."""
    lane_id = st.session_state.get("wb_lane_detail", "")
    truth = build_workbench_truth(repo)
    lane = next((l for l in truth.lanes if l.lane_id == lane_id), None)

    if not lane:
        st.session_state["wb_lane_detail"] = ""
        st.rerun()

    # Back button
    if st.button("← Back to Command Center", key="wb__lane_back"):
        st.session_state["wb_lane_detail"] = ""
        st.rerun()

    st.title(f"{lane.lane_name}")
    st.caption(lane.description)

    if lane_id == "cme_futures":
        _render_cme_detail(lane, repo)
    elif lane_id == "equities_low_float":
        _render_equities_detail(lane, repo)
    elif lane_id == "options_parity":
        _render_options_detail(lane, repo)
    elif lane_id == "crypto":
        _render_crypto_detail(lane, repo)


# ---------------------------------------------------------------------------
# CME detail
# ---------------------------------------------------------------------------


def _render_cme_detail(lane: LaneTruth, repo: Path) -> None:
    st.subheader("CME Futures Symbols")
    st.caption("Seven canonical CME symbols. Select a symbol to view bound models and run campaigns.")

    entries = lane.entries
    if not entries:
        st.info("No CME symbol data available.")
        return

    # Symbol table
    import pandas as pd
    rows = []
    for e in entries:
        e = e  # CmeEntryTruth
        rows.append({
            "Symbol": e.symbol,
            "Data": e.data_status,
            "Events": f"{e.event_count_ready}/{e.event_count}",
            "MBO": e.mbo_status,
            "Models": e.bound_models,
            "Last Campaign": e.latest_campaign[:30] if e.latest_campaign else "—",
            "Blockers": ", ".join(e.blockers[:2]) if e.blockers else "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Per-symbol detail
    selected_symbol = st.selectbox(
        "Symbol detail", [e.symbol for e in entries],
        key="wb__cme_symbol_detail"
    )

    if selected_symbol:
        entry = next(e for e in entries if e.symbol == selected_symbol)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Events", f"{entry.event_count_ready}/{entry.event_count}")
        c2.metric("MBO Status", entry.mbo_status)
        c3.metric("Bound Models", entry.bound_models)
        c4.metric("Data Status", entry.data_status)

        if entry.blockers:
            for b in entry.blockers:
                st.warning(b)
        else:
            c_primary, c_run = st.columns(2)
            with c_primary:
                if st.button("Run Campaign", key="wb__cme_run", type="primary"):
                    st.info("Workbench campaign runner coming in robustness pipeline phase.")
            with c_run:
                if st.button("Dry Run (plan only)", key="wb__cme_dry_run"):
                    st.info("Dry run mode coming.")


# ---------------------------------------------------------------------------
# Equities detail
# ---------------------------------------------------------------------------


def _render_equities_detail(lane: LaneTruth, repo: Path) -> None:
    st.subheader("Low-Float Parabolic Anomaly Sessions")
    st.caption("16 decadal sessions. 3 skipped (pre-2018), 13 live with data.")

    entries = lane.entries
    if not entries:
        st.info("No equities session data available.")
        return

    # Summary stats
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", lane.universe_size)
    c2.metric("Ready", lane.sessions_available)
    c3.metric("Blocked", lane.sessions_blocked)
    c4.metric("Skipped", lane.sessions_total - lane.sessions_available - lane.sessions_blocked)
    c5.metric("Data", f"{lane.data_readiness_pct:.0f}%")

    # Session table
    import pandas as pd
    rows = []
    for e in entries:
        e = e  # EquitiesEntryTruth
        rows.append({
            "Session": e.session_id,
            "Symbol": e.symbol,
            "Date": e.date,
            "Catalyst": e.catalyst,
            "Status": e.status,
            "NDJSON": e.normalized_status,
            "Daily": e.daily_status,
            "Float": e.float_status,
            "L3": e.l3_status,
            "Options": e.option_feature_status,
            "Route": e.route_type or "—",
            "Prediction": e.prediction_status,
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Session detail
    selected = st.selectbox(
        "Session detail", [e.session_id for e in entries],
        key="wb__eq_session_detail"
    )

    if selected:
        entry = next(e for e in entries if e.session_id == selected)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Symbol", entry.symbol)
        c2.metric("Date", entry.date)
        c3.metric("Catalyst", entry.catalyst)
        c4.metric("Dataset", entry.dataset)

        c_d1, c_d2, c_d3, c_d4, c_d5 = st.columns(5)
        c_d1.metric("NDJSON", entry.normalized_status)
        c_d2.metric("Daily", entry.daily_status)
        c_d3.metric("Float", entry.float_status)
        c_d4.metric("Options", entry.option_feature_status)
        c_d5.metric("L3", entry.l3_status)

        if entry.blockers:
            st.subheader("Blockers")
            for b in entry.blockers:
                if "SKIPPED" in b:
                    st.info(b)
                else:
                    st.error(b)

        if entry.prediction_status and entry.prediction_status != "none":
            st.metric("Predictions", entry.prediction_status)

        if entry.status not in ("blocked", "skipped"):
            c_run, c_all = st.columns(2)
            with c_run:
                if st.button("Backtest Session", key="wb__eq_run_session", type="primary"):
                    st.info("Equities backtest integration coming in robustness pipeline.")
            with c_all:
                if st.button("Run All Ready Sessions", key="wb__eq_run_all"):
                    st.info("Mass backtest coming.")


# ---------------------------------------------------------------------------
# Options detail
# ---------------------------------------------------------------------------


def _render_options_detail(lane: LaneTruth, repo: Path) -> None:
    st.subheader("Options Put-Call Parity Groups")
    st.caption("Standalone parity arbitrage. Separate from equities-linked options features.")

    entries = lane.entries
    if not entries:
        st.info("No parity groups configured or data not yet available.")
        st.caption("This lane is separate from the equities-linked options features (which belong to the Equities lane).")
        return

    for e in entries:
        e = e  # OptionsEntryTruth
        with st.container(border=True):
            st.markdown(f"**{e.group_id}** — {e.group_type}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Legs", e.legs)
            c2.metric("Quote data", e.quote_status)
            c3.metric("Backtest", e.backtest_status)

            if e.blockers:
                for b in e.blockers:
                    st.warning(b)
            else:
                if st.button(f"Run Backtest — {e.group_id}", key=f"wb__opt_{e.group_id}", type="primary"):
                    st.info("Options parity backtest integration coming in robustness pipeline phase.")

    st.markdown("---")
    st.caption(
        "Note: Equities-linked options features (OPRA snapshots for stock runner prediction) "
        "are part of the Equities lane, not this Options/Parity lane. "
        "They are separate pipelines."
    )


# ---------------------------------------------------------------------------
# Crypto detail
# ---------------------------------------------------------------------------


def _render_crypto_detail(lane: LaneTruth, repo: Path) -> None:
    st.subheader("Crypto BTC Edge Detection")
    st.caption("7 ML hypotheses on BTC mempool, bookticker, and volatility data from B2/Binance/Deribit.")

    entries = lane.entries
    if not entries:
        st.info("Crypto lane data not yet available.")
        return

    e = entries[0]  # CryptoEntryTruth

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Venue", e.venue_status)
    c2.metric("Gold Data", e.gold_status)
    c3.metric("Mempool", e.mempool_status)
    c4.metric("Bookticker", e.bookticker_status)

    c5, c6 = st.columns(2)
    c5.metric("Hypotheses", e.hypothesis_count)
    c6.metric("Active edge receiver", "active" if e.venue_status == "operational" else "inactive")

    if e.hypothesis_names:
        st.subheader("Hypotheses")
        for h in e.hypothesis_names:
            st.markdown(f"- `{h}`")

    if e.smoke_results:
        st.subheader("Smoke Test Results")
        st.json(e.smoke_results[:5])

    if e.blockers:
        st.subheader("Blockers")
        for b in e.blockers:
            st.warning(b)

    st.caption("Crypto lane uses its own CLI pipeline (`python -m crypto_lane ...`). It is not mixed into the CME futures or equities decision flow.")
