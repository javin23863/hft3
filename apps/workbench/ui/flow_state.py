"""Workbench UI workflow: auto-start campaigns and wire artifacts to tabs."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import streamlit as st

_TERMINAL_STATES = frozenset(
    {
        "pass",
        "fail",
        "blocked",
        "cancelled",
        "conditional",
        "data_insufficient",
        "dry_run",
        "unknown",
        "complete",
        "completed",
    }
)
_AAR_MARKERS = (
    "after_action_response.json",
    "after_action_report.md",
    "after_action_symbolic.json",
    "diagnostics.json",
)

from workbench.ui.workflow_tabs import WORKFLOW_TABS


def navigate_to_tab(tab_name: str) -> None:
    if tab_name in WORKFLOW_TABS:
        st.session_state.wb_ui_tab = tab_name
        st.session_state.wb_nav_hint = f"Next: open the **{tab_name}** tab to continue."


from workbench.src.core.composition import ModelComposition
from workbench.src.run.job_manager import get_job_status, set_control, start_campaign_subprocess


def init_flow_session() -> None:
    if "wb_ui_tab" not in st.session_state:
        st.session_state.wb_ui_tab = WORKFLOW_TABS[0]
    if "wb_nav_hint" not in st.session_state:
        st.session_state.wb_nav_hint = ""
    if "wb_campaign_state" not in st.session_state:
        st.session_state.wb_campaign_state = ""
    if "wb_auto_period" not in st.session_state:
        st.session_state.wb_auto_period = ""
    if "wb_auto_event" not in st.session_state:
        st.session_state.wb_auto_event = ""
    if "wb_chat_messages" not in st.session_state:
        st.session_state.wb_chat_messages = []
    if "wb_chat_context_key" not in st.session_state:
        st.session_state.wb_chat_context_key = ""


from workbench.src.artifacts.paths import campaign_dir_for


def campaign_base(repo: Path, campaign_id: str) -> Path:
    return campaign_dir_for(repo, campaign_id)


def poll_campaign_status(repo: Path, campaign_id: str) -> Dict[str, Any]:
    if not campaign_id:
        return {"state": "idle", "campaign_id": ""}
    status = get_job_status(repo, campaign_id)
    state = str(status.get("state", status.get("status", "unknown"))).lower()
    return {
        "campaign_id": campaign_id,
        "state": state,
        "period": status.get("period", ""),
        "event_id": status.get("event_id", ""),
        "raw": status,
    }


def _event_has_aar(event_dir: Path) -> bool:
    return any((event_dir / name).is_file() for name in _AAR_MARKERS)


def pick_latest_event_with_aar(repo: Path, campaign_id: str) -> Tuple[str, str]:
    """Return (period_label, event_id) for the newest event dir with AAR artifacts."""
    base = campaign_base(repo, campaign_id) / "periods"
    if not base.is_dir():
        return "", ""

    best: Tuple[str, str, float] = ("", "", -1.0)
    for period_dir in sorted(base.iterdir()):
        if not period_dir.is_dir():
            continue
        events_root = period_dir / "events"
        if not events_root.is_dir():
            continue
        period_label = period_dir.name.replace("_", " ")
        for event_dir in events_root.iterdir():
            if not event_dir.is_dir() or not _event_has_aar(event_dir):
                continue
            mtime = max(
                (event_dir / name).stat().st_mtime
                for name in _AAR_MARKERS
                if (event_dir / name).is_file()
            )
            if mtime > best[2]:
                best = (period_label, event_dir.name, mtime)
    return best[0], best[1]


def pick_first_event_with_aar(repo: Path, campaign_id: str) -> Tuple[str, str]:
    """Return first period/event (walk-forward order) with any run artifact."""
    base = campaign_base(repo, campaign_id) / "periods"
    if not base.is_dir():
        return "", ""
    for period_dir in sorted(p.name for p in base.iterdir() if p.is_dir()):
        events_root = base / period_dir / "events"
        if not events_root.is_dir():
            continue
        period_label = period_dir.replace("_", " ")
        for event_dir in sorted(p.name for p in events_root.iterdir() if p.is_dir()):
            if _event_has_aar(events_root / event_dir):
                return period_label, event_dir
    return "", ""


def on_campaign_finished(repo: Path, campaign_id: str) -> Tuple[str, str]:
    period, event_id = pick_first_event_with_aar(repo, campaign_id)
    if not period:
        period, event_id = pick_latest_event_with_aar(repo, campaign_id)
    st.session_state.wb_auto_period = period
    st.session_state.wb_auto_event = event_id
    status = poll_campaign_status(repo, campaign_id)
    st.session_state.wb_campaign_state = status["state"]
    if period and event_id:
        ctx = f"{campaign_id}:{period}:{event_id}"
        if st.session_state.wb_chat_context_key != ctx:
            st.session_state.wb_chat_context_key = ctx
            st.session_state.wb_chat_messages = []
    return period, event_id


def start_campaign_for_selection(
    repo: Path,
    *,
    model_id: str,
    symbol: str,
    composition: Optional[ModelComposition],
    audit_grade: bool = True,
) -> str:
    prior = st.session_state.get("wb_proc")
    prior_cid = st.session_state.get("wb_active_campaign", "")
    if prior_cid:
        set_control(repo, prior_cid, "stop")
    if prior is not None and getattr(prior, "poll", None) and prior.poll() is None:
        try:
            prior.terminate()
        except OSError:
            pass
    proc, cid = start_campaign_subprocess(
        repo,
        model_id=model_id,
        symbol=symbol,
        audit_grade=audit_grade,
        composition=composition,
        trial_mode=not audit_grade,
    )
    st.session_state.wb_proc = proc
    st.session_state.wb_active_campaign = cid
    st.session_state.wb_selected_model = model_id
    st.session_state.wb_selection_explicit = True
    st.session_state.wb_symbol = symbol
    st.session_state.wb_campaign_state = "running"
    st.session_state.wb_auto_period = ""
    st.session_state.wb_auto_event = ""
    st.session_state.wb__period_sel = ""
    st.session_state.wb__event_sel = ""
    st.session_state.wb_period_sel = ""
    st.session_state.wb_event_sel = ""
    st.session_state.wb_chat_messages = []
    st.session_state.wb_chat_context_key = ""
    set_control(repo, cid, "run")
    navigate_to_tab("Backtest Evidence")
    return cid


def resolve_period_event(repo: Path, campaign_id: str) -> Tuple[str, str]:
    """Session-backed period/event for downstream tabs."""
    status = poll_campaign_status(repo, campaign_id) if campaign_id else {"state": "idle"}
    running = status.get("state") == "running"
    manual_period = "" if running else (st.session_state.get("wb__period_sel") or "")
    manual_event = "" if running else (st.session_state.get("wb__event_sel") or "")
    period = manual_period or st.session_state.get("wb_auto_period") or st.session_state.get("wb_period_sel", "")
    event_id = manual_event or st.session_state.get("wb_auto_event") or st.session_state.get("wb_event_sel", "")
    if campaign_id and (not period or not event_id) and not running:
        period, event_id = on_campaign_finished(repo, campaign_id)
    return period, event_id


def workflow_status_strip(repo: Path, model: str, symbol: str, campaign_id: str) -> None:
    status = poll_campaign_status(repo, campaign_id) if campaign_id else {"state": "idle"}
    state = status.get("state", "idle")
    period = status.get("period") or st.session_state.get("wb_auto_period", "")
    event_id = status.get("event_id") or st.session_state.get("wb_auto_event", "")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Model", model or "—")
    c2.metric("Symbol", symbol or "—")
    c3.metric("Campaign", state.upper() if campaign_id else "—")
    detail = campaign_id or "none"
    if period:
        detail = f"{period} / {event_id or '…'}"
    c4.caption(f"Active: `{campaign_id or 'none'}` · {detail}")


@st.fragment(run_every=timedelta(seconds=2))
def campaign_progress_panel(repo: Path, campaign_id: str) -> None:
    if not campaign_id:
        return
    status = poll_campaign_status(repo, campaign_id)
    state = status.get("state", "unknown")
    st.session_state.wb_campaign_state = state

    if state == "running":
        period = status.get("period") or "…"
        event_id = status.get("event_id") or "…"
        st.status(f"Campaign running — {period} / {event_id}", state="running")
        return

    if state in _TERMINAL_STATES and state not in ("idle", "unknown"):
        done_key = f"wb_done_{campaign_id}"
        if st.session_state.get(done_key) != state:
            period, event_id = on_campaign_finished(repo, campaign_id)
            st.session_state[done_key] = state
            if state in ("pass", "completed", "complete", "dry_run"):
                st.success(f"Campaign finished ({state}). Loaded {period or 'summary'} / {event_id or '—'}.")
            elif state == "blocked":
                st.warning("Campaign blocked (missing data or gate). See campaign log in Advanced.")
            elif state == "cancelled":
                st.info("Campaign cancelled.")
            else:
                st.info(f"Campaign state: {state}")
        return

    st.caption(f"Campaign `{campaign_id}` — state `{state}` (waiting for status.json)…")


def event_artifact_dir(repo: Path, campaign_id: str, period: str, event_id: str) -> Optional[Path]:
    if not campaign_id or not period or not event_id:
        return None
    path = (
        campaign_base(repo, campaign_id)
        / "periods"
        / period.replace(" ", "_")
        / "events"
        / event_id
    )
    return path if path.is_dir() else None


def load_json_artifact(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
