"""Streamlit campaign controls and artifact loaders."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.data.event_catalog import campaign_preview, list_personal_events, load_model_binding
from workbench.src.data.personal_lock import is_locked, set_unlocked
from workbench.src.registry.model_catalog import (
    get_catalog_entry,
    load_catalog,
    phase_budget_summary,
    validate_composition,
)
from workbench.src.registry.unified_registry import build_models_config
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile
from workbench.src.run.job_manager import (
    get_job_status,
    list_active_campaigns,
    set_control,
)
from workbench.ui.flow_state import (
    init_flow_session,
    navigate_to_tab,
    start_campaign_for_selection,
    workflow_status_strip,
)
from workbench.ui.workflow_tabs import WORKFLOW_TABS

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
        st.session_state.wb_audit_grade = False
    if "wb_defensive_stubs" not in st.session_state:
        st.session_state.wb_defensive_stubs = []
    if "wb_proc" not in st.session_state:
        st.session_state.wb_proc = None
    init_flow_session()


_RECOMMENDED_STARTERS = [
    ("HYP_5", "Spread blowout / recompression — recommended first run"),
    ("HYP_1", "Second-wave continuation after CPI/NFP impulse"),
    ("PDF_MODEL_1", "Book pressure OFI / MLOFI"),
]


def _runnable_primary_ids(catalog: dict) -> List[str]:
    return sorted(
        [mid for mid, entry in catalog.items() if entry.role in ("alpha", "hybrid")],
        key=lambda mid: catalog[mid].display_name.lower(),
    )


def _render_dataset_panel(repo: Path, model_id: str, symbol: str, cfg) -> None:
    """Always-visible dataset and walk-forward binding for the selected model."""
    binding = load_model_binding(repo, model_id)
    preview = campaign_preview(model_id, symbol, repo)
    datasets = binding.get("required_datasets") or cfg.required_datasets or ["mbo_npz"]
    npz_root = repo / "data" / "npz"
    dataset_note = "mbo_npz" if "mbo_npz" in datasets else ", ".join(datasets)

    st.subheader("Dataset & walk-forward binding")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Symbol", symbol)
    c2.metric("Latency lane", cfg.latency_lane)
    c3.metric("Dataset lane", dataset_note)
    c4.metric("History years", f"{preview.get('catalog_years', 0)}/{cfg.min_history_years}")

    if "mbo_npz" in datasets:
        st.caption(f"NPZ path pattern: `{npz_root}/{symbol}_{{event_id}}_mbo.npz` · ")
    else:
        st.caption(f"Required datasets: {', '.join(datasets)} (see model_event_binding.yaml)")
    st.caption(
        f"Contexts: {', '.join(sorted(binding['allowed_contexts']))}"
    )

    summary_rows = []
    total_events = 0
    ready_events = 0
    for period_name, pdata in preview.get("periods", {}).items():
        events = pdata.get("events", [])
        ready = sum(1 for e in events if e.get("npz_present"))
        total = len(events)
        total_events += total
        ready_events += ready
        summary_rows.append(
            {
                "period": period_name,
                "years": f"{pdata.get('start_year')}–{pdata.get('end_year')}",
                "events": total,
                "npz_ready": ready,
                "npz_missing": total - ready,
            }
        )
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
        if ready_events == 0:
            st.error("No NPZ files on disk for this model/symbol — use **Download missing** or backfill catalog.")
        elif ready_events < total_events:
            st.warning(f"{total_events - ready_events} of {total_events} events missing NPZ (campaign may block).")
        else:
            st.success(f"All {total_events} walk-forward events have NPZ data ready.")
    else:
        st.error("No events in catalog for this model/symbol binding.")

    detail_rows = []
    for period_name, pdata in preview.get("periods", {}).items():
        for ev in pdata.get("events", []):
            detail_rows.append(
                {
                    "period": period_name,
                    "event_id": ev.get("event_id"),
                    "release": ev.get("release_date"),
                    "context": ev.get("event_context"),
                    "npz": "ready" if ev.get("npz_present") else "missing",
                }
            )
    if detail_rows:
        with st.expander("Event-level catalog", expanded=ready_events < total_events):
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)


def _render_data_preview(repo: Path, model_id: str, symbol: str) -> None:
    cfg = build_models_config()[model_id]
    _render_dataset_panel(repo, model_id, symbol, cfg)


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
                    "Set primary",
                    key=_catalog_widget_key(prefix, "set", str(row_idx), entry.model_id),
                ):
                    st.session_state.wb_selected_model = entry.model_id
                    navigate_to_tab("Backtest Results")
                    st.rerun()
                if st.button(
                    "Run campaign",
                    key=_catalog_widget_key(prefix, "run", str(row_idx), entry.model_id),
                    type="primary",
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


def model_selector_panel(repo: Path) -> Tuple[str, str, str]:
    configs = build_models_config()
    catalog = load_catalog(repo)
    runnable = _runnable_primary_ids(catalog)

    st.caption(
        "Trial mode: runs available NPZ events only (skips WFC matrix). "
        "Latency lane and datasets come from each model's registry binding."
    )

    symbol = st.selectbox("Symbol", ["MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0"], key="wb__sym_pick")
    st.session_state.wb_symbol = symbol

    st.subheader("Quick start")
    starter_cols = st.columns(len(_RECOMMENDED_STARTERS))
    for col, (mid, blurb) in zip(starter_cols, _RECOMMENDED_STARTERS):
        if mid not in catalog:
            continue
        entry = catalog[mid]
        with col:
            st.markdown(f"**{entry.display_name}**")
            st.caption(blurb)
            st.caption(f"`{mid}` · lane {configs[mid].latency_lane}")
            if st.button("Start with this model", key=f"wb__starter__{mid}", type="primary"):
                st.session_state.wb_selected_model = mid
                composition = get_session_composition(mid)
                cid = start_campaign_for_selection(
                    repo,
                    model_id=mid,
                    symbol=st.session_state.wb_symbol,
                    composition=composition,
                    audit_grade=st.session_state.wb_audit_grade,
                )
                st.toast(f"Campaign started: {cid}")
                st.rerun()

    if not runnable:
        st.error("No runnable alpha/hybrid models in catalog.")
        return "", symbol, st.session_state.wb_active_campaign or ""

    default_model = st.session_state.wb_selected_model
    if default_model not in runnable:
        default_model = "HYP_5" if "HYP_5" in runnable else runnable[0]
        st.session_state.wb_selected_model = default_model

    picked = st.selectbox(
        "Primary model",
        runnable,
        index=runnable.index(st.session_state.wb_selected_model),
        format_func=lambda mid: f"{catalog[mid].display_name} ({mid})",
        key="wb__primary_model",
    )
    st.session_state.wb_selected_model = picked
    model = picked

    camp = st.session_state.wb_active_campaign or ""
    workflow_status_strip(repo, model, symbol, camp)

    cfg = configs[model]
    _render_dataset_panel(repo, model, symbol, cfg)

    composition = get_session_composition(model)
    st.caption(f"Defensive stubs in stack: {len(composition.defensive_stubs)} (optional — expand below)")

    c_primary, c_run, c_pause, c_stop = st.columns(4)
    with c_primary:
        if st.button("Set primary", key="wb__set_primary", use_container_width=True):
            st.session_state.wb_selected_model = model
            navigate_to_tab("Backtest Results")
            st.rerun()
    with c_run:
        if st.button("Run campaign", key="wb__start_campaign", type="primary", use_container_width=True):
            cid = start_campaign_for_selection(
                repo,
                model_id=model,
                symbol=symbol,
                composition=composition,
                audit_grade=st.session_state.wb_audit_grade,
            )
            st.toast(f"Campaign started: {cid}")
            st.rerun()
    with c_pause:
        if st.button("Pause", key="wb__pause", use_container_width=True) and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "pause")
    with c_stop:
        if st.button("Stop", key="wb__stop", use_container_width=True) and st.session_state.wb_active_campaign:
            set_control(repo, st.session_state.wb_active_campaign, "stop")

    if st.button("Download missing NPZ", key="wb__download_missing"):
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

    with st.expander("Advanced — audit grade & full model grid"):
        audit = st.checkbox(
            "Audit grade (full-sweep + history gate)",
            value=st.session_state.wb_audit_grade,
            key="wb__audit",
        )
        st.session_state.wb_audit_grade = audit
        st.markdown("**All models**")
        all_entries = [catalog[mid] for mid in sorted(catalog.keys())]
        _render_catalog_rows(
            repo,
            all_entries,
            configs,
            key_prefix="all_catalog",
            symbol=symbol,
            audit_grade=audit,
        )

    with st.expander("Defensive stack builder"):
        stack_builder_panel(repo, model)

    with st.expander("Advanced — load prior campaign"):
        campaigns = list_active_campaigns(repo)
        picked_camp = st.selectbox("Load campaign", [""] + campaigns, key="wb__camp_pick")
        if picked_camp:
            st.session_state.wb_active_campaign = picked_camp
            camp = picked_camp
            status = get_job_status(repo, picked_camp)
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
