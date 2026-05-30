"""Streamlit campaign controls and artifact loaders."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yaml

from workbench.src.data.event_catalog import campaign_preview, list_personal_events, load_model_binding
from workbench.src.data.personal_lock import is_locked, set_unlocked
from workbench.src.registry.unified_registry import build_models_config, list_models
from workbench.src.run.job_manager import (
    get_job_status,
    list_active_campaigns,
    set_control,
    start_campaign_subprocess,
)

_LANES = ["all", "sub_10ms", "10_250ms", "multi_second", "options_chain", "microsecond"]


def init_session(repo: Path) -> None:
    if "wb_repo" not in st.session_state:
        st.session_state.wb_repo = repo
    if "wb_selected_model" not in st.session_state:
        st.session_state.wb_selected_model = ""
    if "wb_active_campaign" not in st.session_state:
        st.session_state.wb_active_campaign = ""
    if "wb_symbol" not in st.session_state:
        st.session_state.wb_symbol = "MES.v.0"
    if "wb_audit_grade" not in st.session_state:
        st.session_state.wb_audit_grade = True
    if "wb_lane_filter" not in st.session_state:
        st.session_state.wb_lane_filter = "all"
    if "wb_proc" not in st.session_state:
        st.session_state.wb_proc = None


def personal_lock_sidebar(repo: Path) -> None:
    locked = is_locked(repo)
    st.subheader("Personal sandbox lock")
    if locked:
        st.error("Locked — 2026-03-01…2026-05-30 hidden from promotion")
    else:
        st.success("Unlocked — personal replay enabled (never promotes)")
    unlock = st.checkbox("Unlock personal sandbox (local only)", value=not locked, key="personal_unlock")
    if unlock != (not locked):
        set_unlocked(repo, unlock)
        st.rerun()


def _binding_meta(repo: Path, model_id: str) -> dict:
    binding_path = repo / "workbench" / "config" / "model_event_binding.yaml"
    raw = yaml.safe_load(binding_path.read_text(encoding="utf-8")) or {}
    if model_id.startswith("PDF_MODEL_"):
        return raw.get("pdf", {}).get(model_id, {})
    return raw.get("hypothesis", {}).get(model_id, {})


def _filter_models(models: List[str], lane: str, configs: dict) -> List[str]:
    if lane == "all":
        return models
    out = []
    for mid in models:
        cfg = configs[mid]
        binding = _binding_meta(Path(st.session_state.wb_repo), mid)
        datasets = binding.get("required_datasets") or cfg.required_datasets
        if lane == "options_chain" and "options_chain" in datasets:
            out.append(mid)
        elif cfg.latency_lane == lane:
            out.append(mid)
    return out


def model_selector_panel(repo: Path) -> Tuple[str, str, str]:
    configs = build_models_config()
    models = sorted(list_models())
    lane = st.selectbox("Latency lane filter", _LANES, key="lane_filter")
    st.session_state.wb_lane_filter = lane
    filtered = _filter_models(models, lane, configs)
    st.metric("Registered models", len(models))
    st.caption(f"Showing {len(filtered)} after lane filter")

    selected = st.selectbox("Select model", [""] + filtered, key="model_pick")
    if selected:
        st.session_state.wb_selected_model = selected

    symbol = st.selectbox("Primary symbol", ["MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0"], key="sym_pick")
    st.session_state.wb_symbol = symbol
    audit = st.checkbox("Audit grade (full-sweep + history gate)", value=st.session_state.wb_audit_grade)
    st.session_state.wb_audit_grade = audit

    model = st.session_state.wb_selected_model
    if model:
        cfg = configs[model]
        binding = load_model_binding(repo, model)
        meta = _binding_meta(repo, model)
        st.caption(
            f"Lane: {cfg.latency_lane} | datasets: {meta.get('required_datasets', cfg.required_datasets)} | "
            f"mode: {binding.get('campaign_mode', 'mbo')}"
        )
        contexts = sorted(binding["allowed_contexts"])
        st.caption(f"Bound contexts: {', '.join(contexts) or 'none'}")
        preview = campaign_preview(model, symbol, repo)
        cpi_nfp = sum(
            1
            for pdata in preview["periods"].values()
            for ev in pdata["events"]
            if "CPI" in ev["event_id"] or "NFP" in ev["event_id"]
        )
        st.caption(
            f"Stages: {len(preview['periods'])} | Catalog years: {preview['catalog_years']}/{cfg.min_history_years} | "
            f"CPI+NFP events in catalog: {cpi_nfp}"
        )
        rows = []
        for pname, pdata in preview["periods"].items():
            for ev in pdata["events"]:
                rows.append(
                    {
                        "stage": pname,
                        "event_id": ev["event_id"],
                        "release_date": ev["release_date"],
                        "context": ev["event_context"],
                        "npz": "yes" if ev["npz_present"] else "missing",
                    }
                )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=200)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("Start Campaign", disabled=not model):
            proc, cid = start_campaign_subprocess(
                repo,
                model_id=model,
                symbol=symbol,
                audit_grade=audit,
            )
            st.session_state.wb_proc = proc
            st.session_state.wb_active_campaign = cid
            set_control(repo, cid, "run")
            st.info(f"Campaign started: {cid}")
    with col2:
        if st.button("Pause") and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "pause")
    with col3:
        if st.button("Stop") and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "stop")
    with col4:
        if st.button("Download missing", disabled=not model):
            cmd = [
                sys.executable,
                str(repo / "workbench" / "scripts" / "backfill_catalog.py"),
                "--model",
                model,
                "--symbol",
                symbol,
                "--download-missing",
                "--max-cost-usd",
                "25",
            ]
            subprocess.Popen(cmd, cwd=str(repo))
            st.info("Backfill started (max $25)")

    campaigns = list_active_campaigns(repo)
    camp = st.selectbox("Load campaign", [""] + campaigns, key="camp_pick")
    if camp:
        st.session_state.wb_active_campaign = camp
        status = get_job_status(repo, camp)
        st.json(status)

    return model, symbol, camp


def personal_runs_panel(repo: Path, model: str, symbol: str) -> None:
    st.subheader("Personal runs (sandbox)")
    if is_locked(repo):
        st.warning("Personal sandbox locked — unlock in sidebar to preview 2026 events.")
        return
    if not model:
        st.info("Select a model above.")
        return
    events = list_personal_events(model, symbol, repo)
    if not events:
        st.caption("No personal sandbox events in catalog for this model/symbol.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "event_id": e.event_id,
                    "release_date": e.release_date,
                    "context": e.event_context,
                    "npz": "yes" if e.npz_present else "missing",
                }
                for e in events
            ]
        ),
        use_container_width=True,
    )


def load_campaign_diagnostics(repo: Path, campaign_id: str, period: str = "", event_id: str = "") -> Optional[Dict[str, Any]]:
    if not campaign_id:
        return None
    base = repo / "research_cards" / "workbench_runs" / campaign_id
    if period and event_id:
        path = base / "periods" / period.replace(" ", "_") / "events" / event_id / "diagnostics.json"
    elif period:
        path = base / "periods" / period.replace(" ", "_") / "period_summary.json"
    else:
        path = base / "summary.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def campaign_periods(repo: Path, campaign_id: str) -> List[str]:
    base = repo / "research_cards" / "workbench_runs" / campaign_id / "periods"
    if not base.is_dir():
        return []
    return sorted(p.name.replace("_", " ") for p in base.iterdir() if p.is_dir())


def campaign_events(repo: Path, campaign_id: str, period: str) -> List[str]:
    base = repo / "research_cards" / "workbench_runs" / campaign_id / "periods" / period.replace(" ", "_") / "events"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())
