"""Streamlit microstructure workbench UI."""

from __future__ import annotations

import json
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


def _query_param_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).strip()


def _resolve_model_query(value: str) -> str:
    if not value:
        return ""
    try:
        from features_engine.src.model_registry import resolve_model_id

        return resolve_model_id(value)
    except KeyError:
        return ""
    except Exception:
        return value


def _vbt_paid_has_anomalies(status: dict) -> bool:
    anomalies = status.get("anomalies")
    if isinstance(anomalies, str):
        if anomalies.strip():
            return True
    elif anomalies:
        return True
    validation_errors = status.get("validation_errors")
    if isinstance(validation_errors, str):
        if validation_errors.strip():
            return True
    elif validation_errors:
        return True
    validation_error_count = status.get("validation_error_count")
    return (
        isinstance(validation_error_count, (int, float))
        and not isinstance(validation_error_count, bool)
        and validation_error_count > 0
    )


def _normalize_vbt_paid_state(
    state: object,
    *,
    expected: float | int | None,
    completed: float | int | None,
    failed: float | int | None,
    skipped: float | int | None,
    accounted: float | int | None,
    has_anomalies: bool,
) -> str:
    display_state = str(state or "unknown").strip().lower() or "unknown"
    has_rejected_units = (failed is not None and failed > 0) or (
        skipped is not None and skipped > 0
    )
    if has_rejected_units:
        return "partial_failed"
    if display_state != "complete":
        return display_state
    clean_complete = (
        expected is not None
        and expected > 0
        and completed is not None
        and failed is not None
        and skipped is not None
        and failed == 0
        and skipped == 0
        and accounted == expected
        and not has_anomalies
    )
    return "complete" if clean_complete else "stalled"


init_session(REPO)

with st.sidebar:
    personal_lock_sidebar(REPO)
    run_sources = workbench_run_sources()
    raw_query_source = st.query_params.get("source", "")
    if isinstance(raw_query_source, list):
        raw_query_source = raw_query_source[0] if raw_query_source else ""
    query_source = str(raw_query_source).strip()
    if query_source in run_sources:
        st.session_state.wb_run_source = query_source
    raw_query_model = _query_param_value("model")
    query_model = (
        _resolve_model_query(raw_query_model)
        if raw_query_model and raw_query_model != st.session_state.get("wb_consumed_query_model", "")
        else ""
    )
    if query_model:
        st.session_state.wb_selected_model = query_model
        st.session_state.wb_selection_explicit = True
        st.session_state.wb__primary_model = query_model
        st.session_state.wb_consumed_query_model = raw_query_model
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


def _render_vbt_paid_status(repo: Path) -> None:
    def _first_present(*names: str, default=None):
        for name in names:
            value = status.get(name)
            if value is not None:
                return value
        return default

    def _number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def _display(value):
        return "--" if value is None else value

    status_path = repo / "runtime" / "reports" / "vbt_full_status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        st.caption("VBT paid-screen status unavailable — no vbt_full_status.json synced yet.")
        return
    except (json.JSONDecodeError, OSError, ValueError):
        st.caption("VBT paid-screen status unreadable — vbt_full_status.json is malformed or partially written.")
        return
    if not isinstance(status, dict):
        st.caption("VBT paid-screen status malformed — expected a JSON object.")
        return
    state = _first_present("state", "status", default="unknown")
    workers = _first_present("workers")
    rate = _first_present("units_per_hour")
    expected = _number(_first_present("expected_work_units", "expected"))
    completed = _number(_first_present("completed_work_units", "completed"))
    failed = _number(_first_present("failed_work_units", "failed"))
    skipped = _number(_first_present("skipped_work_units", "skipped"))
    counts = (completed, failed, skipped)
    accounted = None if any(value is None for value in counts) else sum(counts)
    display_state = _normalize_vbt_paid_state(
        state,
        expected=expected,
        completed=completed,
        failed=failed,
        skipped=skipped,
        accounted=accounted,
        has_anomalies=_vbt_paid_has_anomalies(status),
    )
    expected_display = _display(expected)
    progress = None
    if expected is not None and expected > 0 and accounted is not None:
        progress = min(1.0, max(0.0, accounted / expected))
    with st.expander("VectorBT paid screen (Vast)", expanded=state == "running"):
        cols = st.columns(7)
        cols[0].metric("state", display_state)
        cols[1].metric("workers", _display(workers))
        cols[2].metric("completed", f"{_display(completed)}/{expected_display}")
        cols[3].metric("failed", _display(failed))
        cols[4].metric("skipped", _display(skipped))
        cols[5].metric("accounted", f"{_display(accounted)}/{expected_display}")
        cols[6].metric("rate", f"{_display(rate)}/h")
        if progress is not None:
            st.progress(progress)
        st.caption(
            " · ".join(
                str(v)
                for v in (
                    status.get("run_id"),
                    status.get("eta_utc"),
                    status.get("last_sync_utc"),
                    status.get("tmux_session"),
                )
                if v
            )
        )


_render_vbt_paid_status(REPO)

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
    pass

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
    st.divider()
    workbench_llm_console(snapshot)
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
