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

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.data.event_catalog import campaign_preview, list_personal_events, load_model_binding
from workbench.src.data.personal_lock import is_locked, set_unlocked
from workbench.src.registry.model_catalog import (
    get_catalog_entry,
    list_by_role,
    load_catalog,
    phase_budget_summary,
    validate_composition,
)
from workbench.src.registry.unified_registry import build_models_config, list_models
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile
from workbench.src.run.job_manager import (
    get_job_status,
    list_active_campaigns,
    set_control,
)
from workbench.ui.flow_state import (
    init_flow_session,
    start_campaign_for_selection,
    workflow_status_strip,
)

_LANES = ["all", "sub_10ms", "10_250ms", "multi_second", "options_chain", "microsecond"]
_ROLES = ["all", "alpha", "defensive", "hybrid"]
_PHASES = ["before", "during", "after", "continuous"]


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
    if "wb_role_filter" not in st.session_state:
        st.session_state.wb_role_filter = "all"
    if "wb_defensive_stubs" not in st.session_state:
        st.session_state.wb_defensive_stubs = []
    if "wb_proc" not in st.session_state:
        st.session_state.wb_proc = None
    init_flow_session()


def _render_data_preview(repo: Path, model_id: str, symbol: str) -> None:
    preview = campaign_preview(model_id, symbol, repo)
    rows = []
    for period_name, pdata in preview.get("periods", {}).items():
        for ev in pdata.get("events", []):
            rows.append(
                {
                    "period": period_name,
                    "event_id": ev.get("event_id"),
                    "npz": "yes" if ev.get("npz_present") else "missing",
                }
            )
    with st.expander("Data availability preview", expanded=bool(rows) and any(r["npz"] == "missing" for r in rows)):
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No catalog events for this model/symbol binding.")


def get_session_composition(primary: str) -> ModelComposition:
    stubs = [
        DefensiveStub(
            model_id=s["model_id"],
            phase=s["phase"],
            budget_us=float(s["budget_us"]),
            enabled=bool(s.get("enabled", True)),
        )
        for s in st.session_state.wb_defensive_stubs
    ]
    return ModelComposition(primary_model_id=primary, defensive_stubs=stubs)


def _catalog_widget_key(key_prefix: str, *parts: str) -> str:
    prefix = key_prefix.strip()
    if not prefix:
        raise ValueError("_render_catalog_rows requires a non-empty unique key_prefix")
    return "_".join((prefix, *parts))


def _render_catalog_rows(
    repo: Path,
    entries: list,
    configs: dict,
    *,
    key_prefix: str,
    symbol: str,
    audit_grade: bool,
) -> None:
    prefix = key_prefix.strip()
    if not prefix:
        raise ValueError("_render_catalog_rows requires a non-empty unique key_prefix")

    search = st.text_input(
        "Search models",
        key=_catalog_widget_key(prefix, "catalog_search"),
    ).strip().lower()

    deduped = []
    seen: set[str] = set()
    for entry in entries:
        if entry.model_id in seen:
            continue
        seen.add(entry.model_id)
        deduped.append(entry)

    for row_idx, entry in enumerate(deduped):
        cfg = configs.get(entry.model_id)
        lane = cfg.latency_lane if cfg else "?"
        if search and search not in entry.display_name.lower() and search not in entry.model_id.lower():
            continue
        cols = st.columns([3, 1, 1])
        with cols[0]:
            st.markdown(f"**{entry.display_name}** `{entry.model_id}`")
            st.caption(entry.description)
        with cols[1]:
            st.caption(f"Lane: {lane}")
            st.caption(f"Role: {entry.role}")
        with cols[2]:
            primary = st.session_state.wb_selected_model
            if entry.role != "defensive":
                if st.button(
                    "Select & run campaign",
                    key=_catalog_widget_key(prefix, "select", str(row_idx), entry.model_id),
                ):
                    st.session_state.wb_selected_model = entry.model_id
                    composition = get_session_composition(entry.model_id)
                    cid = start_campaign_for_selection(
                        repo,
                        model_id=entry.model_id,
                        symbol=symbol,
                        composition=composition,
                        audit_grade=audit_grade,
                    )
                    st.toast(f"Campaign started: {cid}")
                    st.rerun()
            else:
                stub_label = "Add to stack & re-run" if primary else "Add to stack"
                if st.button(
                    stub_label,
                    key=_catalog_widget_key(prefix, "enable", str(row_idx), entry.model_id),
                ):
                    st.session_state.wb_defensive_stubs.append(
                        {
                            "model_id": entry.model_id,
                            "phase": entry.default_phase,
                            "budget_us": entry.budget_us,
                            "enabled": True,
                        }
                    )
                    if primary:
                        composition = get_session_composition(primary)
                        cid = start_campaign_for_selection(
                            repo,
                            model_id=primary,
                            symbol=symbol,
                            composition=composition,
                            audit_grade=audit_grade,
                        )
                        st.toast(f"Campaign re-started: {cid}")
                    st.rerun()
        st.divider()


def stack_builder_panel(repo: Path, primary: str) -> ModelComposition:
    st.subheader("Defensive stack builder")
    composition = get_session_composition(primary) if primary else ModelComposition("", [])
    if not primary:
        st.info("Select a primary alpha model from the catalog.")
        return composition

    stubs = st.session_state.wb_defensive_stubs
    if stubs:
        for i, stub in enumerate(list(stubs)):
            cols = st.columns([2, 1, 1, 1])
            entry = get_catalog_entry(stub["model_id"], repo)
            cols[0].write(f"`{stub['model_id']}` — {entry.display_name}")
            stub["phase"] = cols[1].selectbox(
                "Phase",
                _PHASES,
                index=_PHASES.index(stub.get("phase", entry.default_phase)),
                key=f"wb__stack__phase__{i}__{stub['model_id']}",
            )
            stub["budget_us"] = cols[2].number_input(
                "Budget µs",
                min_value=1.0,
                value=float(stub.get("budget_us", entry.budget_us)),
                key=f"wb__stack__budget__{i}__{stub['model_id']}",
            )
            if cols[3].button("Remove", key=f"wb__stack__remove__{i}__{stub['model_id']}"):
                stubs.pop(i)
                st.rerun()
    else:
        st.caption("No defensive stubs — campaign runs primary only.")

    composition = get_session_composition(primary)
    errs = validate_composition(composition, repo)
    if errs:
        for e in errs:
            st.warning(e)

    phase_totals = phase_budget_summary(composition, repo)
    cpp_p99 = CppLatencyProfile.from_yaml_defaults().measured_production_p99_us
    decision_path = phase_totals.get("before", 0) + phase_totals.get("during", 0) + cpp_p99
    st.markdown("**Phase timing summary**")
    st.dataframe(
        pd.DataFrame(
            [
                {"phase": p, "budget_us": phase_totals.get(p, 0.0), "budget_ms": phase_totals.get(p, 0.0) / 1000.0}
                for p in _PHASES
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"Decision path ≈ {decision_path/1000:.2f} ms (before + during stubs + C++ p99 {cpp_p99:.0f}µs)"
    )
    if st.button("Load preset: VPIN + Quantum + Hawkes", key="wb__load_preset"):
        st.session_state.wb_defensive_stubs = [
            {"model_id": "PDF_MODEL_3", "phase": "continuous", "budget_us": 2500, "enabled": True},
            {"model_id": "PDF_MODEL_9", "phase": "before", "budget_us": 50, "enabled": True},
            {"model_id": "PDF_MODEL_11", "phase": "during", "budget_us": 2500, "enabled": True},
        ]
        st.rerun()
    return composition


def personal_lock_sidebar(repo: Path) -> None:
    locked = is_locked(repo)
    st.subheader("Personal sandbox lock")
    if locked:
        st.info("Locked — personal sandbox dates (2026-03-01…2026-05-30) are hidden from promotion.")
    else:
        st.success("Unlocked — personal replay enabled (never promotes)")
    unlock = st.checkbox("Unlock personal sandbox (local only)", value=not locked, key="wb__personal_unlock")
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
    catalog = load_catalog(repo)
    models = sorted(list_models())

    colf1, colf2 = st.columns(2)
    with colf1:
        lane = st.selectbox("Latency lane filter", _LANES, key="wb__lane_filter")
    with colf2:
        role_filter = st.selectbox("Role filter", _ROLES, key="wb__role_filter")
    st.session_state.wb_lane_filter = lane
    st.session_state.wb_role_filter = role_filter

    filtered_ids = _filter_models(models, lane, configs)
    entries = [catalog[mid] for mid in filtered_ids if mid in catalog]
    if role_filter != "all":
        entries = [e for e in entries if e.role == role_filter]

    st.metric("Registered models", len(models))
    st.caption(f"Showing {len(entries)} after filters")

    symbol = st.selectbox("Primary symbol", ["MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0"], key="wb__sym_pick")
    st.session_state.wb_symbol = symbol

    model = st.session_state.wb_selected_model
    camp = st.session_state.wb_active_campaign or ""
    workflow_status_strip(repo, model, symbol, camp)

    with st.expander("Advanced options"):
        audit = st.checkbox(
            "Audit grade (full-sweep + history gate)",
            value=st.session_state.wb_audit_grade,
            key="wb__audit",
        )
        st.session_state.wb_audit_grade = audit
    audit = st.session_state.wb_audit_grade

    tab_alpha, tab_hybrid, tab_defensive, tab_stack = st.tabs(
        ["Alpha catalog", "Hybrid catalog", "Defensive catalog", "Stack builder"]
    )
    with tab_alpha:
        alpha_entries = [e for e in entries if e.role == "alpha"]
        _render_catalog_rows(
            repo,
            alpha_entries,
            configs,
            key_prefix="alpha_catalog",
            symbol=symbol,
            audit_grade=audit,
        )
    with tab_hybrid:
        hybrid_entries = [e for e in entries if e.role == "hybrid"]
        _render_catalog_rows(
            repo,
            hybrid_entries,
            configs,
            key_prefix="hybrid_catalog",
            symbol=symbol,
            audit_grade=audit,
        )
    with tab_defensive:
        def_entries = list_by_role("defensive", repo)
        if role_filter == "all" or role_filter == "defensive":
            def_entries = [e for e in def_entries if e.model_id in filtered_ids or lane == "all"]
        _render_catalog_rows(
            repo,
            def_entries,
            configs,
            key_prefix="defensive_catalog",
            symbol=symbol,
            audit_grade=audit,
        )

    with tab_stack:
        composition = stack_builder_panel(repo, model)

    if model:
        st.success(f"Primary: **{catalog[model].display_name}** (`{model}`)")
        cfg = configs[model]
        binding = load_model_binding(repo, model)
        preview = campaign_preview(model, symbol, repo)
        cpi_nfp = sum(
            1
            for pdata in preview["periods"].values()
            for ev in pdata["events"]
            if "CPI" in ev["event_id"] or "NFP" in ev["event_id"]
        )
        st.caption(
            f"Lane: {cfg.latency_lane} | Stubs: {len(composition.defensive_stubs)} | "
            f"Catalog years: {preview['catalog_years']}/{cfg.min_history_years} | CPI+NFP: {cpi_nfp}"
        )
        st.caption(f"Contexts: {', '.join(sorted(binding['allowed_contexts']))}")
        _render_data_preview(repo, model, symbol)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("Re-run campaign", disabled=not model, key="wb__start_campaign"):
            composition = get_session_composition(model) if model else None
            cid = start_campaign_for_selection(
                repo,
                model_id=model,
                symbol=symbol,
                composition=composition,
                audit_grade=audit,
            )
            st.toast(f"Campaign re-started: {cid}")
            st.rerun()
    with col2:
        if st.button("Pause", key="wb__pause") and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "pause")
    with col3:
        if st.button("Stop", key="wb__stop") and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "stop")
    with col4:
        if st.button("Download missing", disabled=not model, key="wb__download_missing"):
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

    with st.expander("Advanced — load prior campaign"):
        campaigns = list_active_campaigns(repo)
        picked = st.selectbox("Load campaign", [""] + campaigns, key="wb__camp_pick")
        if picked:
            st.session_state.wb_active_campaign = picked
            camp = picked
            status = get_job_status(repo, picked)
            st.json(status)

    return model, symbol, camp


def personal_runs_panel(repo: Path, model: str, symbol: str) -> None:
    st.subheader("Personal runs (sandbox)")
    if is_locked(repo):
        st.info("Personal sandbox locked — unlock in sidebar to preview 2026 events.")
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
