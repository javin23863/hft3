"""Autonomous runner monitor panels for the Workbench UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from hft3_bootstrap import pythonpath_entries
from workbench.src.run.crypto_smoke_runner import latest_status_path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed(start: str | None, end: str | None = None) -> str:
    started = _parse_ts(start)
    if not started:
        return "—"
    finished = _parse_ts(end) or datetime.now(timezone.utc)
    seconds = max(0, int((finished - started).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _crypto_registry_snapshot(repo: Path) -> dict[str, Any]:
    try:
        from crypto_lane.src.config_loader import load_hypotheses
        from crypto_lane.src.ml.candidate_registry import discover_backtest_configs, discover_candidates

        candidates = discover_candidates()
        backtests = discover_backtest_configs()
        hypotheses = load_hypotheses()
        return {
            "ok": True,
            "hypotheses": [h.get("hypothesis_id", "") for h in hypotheses],
            "candidates": [c.get("candidate_id", "") for c in candidates],
            "backtests": [b.get("config_id", "") for b in backtests],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hypotheses": [], "candidates": [], "backtests": []}


def _latest_crypto_reports(repo: Path) -> list[dict[str, Any]]:
    status = _read_json(latest_status_path(repo))
    run_id = str(status.get("run_id") or "")
    run_reports = latest_status_path(repo).parent / run_id / "smoke_reports"
    run_local = run_reports.is_dir()
    root = run_reports if run_local else repo / "research_cards" / "crypto"
    reports: list[dict[str, Any]] = []
    if not root.is_dir():
        return reports
    pattern = "*.json" if run_local else "*/smoke_report.json"
    for path in sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        payload = _read_json(path)
        if not payload:
            continue
        primary = (payload.get("runs") or {}).get("with_btc_node") or (payload.get("runs") or {}).get("without_btc_node") or {}
        reports.append(
            {
                "candidate_id": payload.get("candidate_id", path.parent.name),
                "hypothesis_id": payload.get("hypothesis_id", ""),
                "pass_fail": payload.get("pass_fail", "unknown"),
                "oos_ic": float(primary.get("oos_ic_baseline_mean", 0.0)),
                "n_rows": int(primary.get("n_rows", 0) or 0),
                "n_folds": int(primary.get("n_folds", 0) or 0),
                "holdout": (payload.get("holdout_gate") or {}).get("status", ""),
                "ack_gate": payload.get("execution_ack_status", ""),
                "run_id": run_id if run_local else "",
                "report": str(path),
            }
        )
    return reports


def _start_crypto_smoke(repo: Path, candidate: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries(repo))
    env.setdefault("HFT3_CPP_STACK_VERIFY", "off")
    cmd = [sys.executable, "-m", "workbench", "crypto-smoke"]
    if candidate:
        cmd.extend(["--candidate", candidate])
    return subprocess.Popen(cmd, cwd=str(repo), env=env)


def _status_metrics(status: dict[str, Any], registry: dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    state = str(status.get("state") or "idle").lower()
    state_label = {
        "completed": "DONE",
        "running": "RUN",
        "blocked": "BLOCKED",
        "failed": "FAILED",
    }.get(state, state.upper() or "IDLE")
    stage = str(status.get("current_stage") or "—").replace("_", " ").title()
    c1.metric("State", state_label)
    c2.metric("Stage", stage)
    done = int(status.get("completed_candidates") or 0)
    total = int(status.get("total_candidates") or len(registry.get("candidates", [])) or 0)
    c3.metric("Candidates", f"{done}/{total}" if total else "0")
    c4.metric("Elapsed", _elapsed(status.get("started_at"), status.get("finished_at")))
    if total:
        st.progress(min(1.0, done / total), text=f"{done} of {total} candidates evaluated")


def _render_stages(status: dict[str, Any]) -> None:
    rows = []
    for stage in status.get("stages") or []:
        rows.append(
            {
                "stage": stage.get("name", ""),
                "status": stage.get("status", ""),
                "elapsed": _elapsed(stage.get("started_at"), stage.get("finished_at")),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_candidates(status: dict[str, Any]) -> None:
    candidates = status.get("candidates") or []
    if not candidates:
        return
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "candidate": candidate.get("candidate_id", ""),
                "hypothesis": candidate.get("hypothesis_id", ""),
                "state": candidate.get("status", ""),
                "gate": candidate.get("pass_fail", ""),
                "oos_ic": candidate.get("oos_ic"),
                "rows": candidate.get("n_rows"),
                "folds": candidate.get("n_folds"),
                "holdout": candidate.get("holdout_status", ""),
                "negative_controls": candidate.get("negative_controls_ok"),
                "execution_ack": candidate.get("order_ack_status", ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_decision(status: dict[str, Any]) -> None:
    decision = status.get("decision") or {}
    if not decision:
        return
    action = str(decision.get("action", ""))
    text = f"{action}: {decision.get('reason', '')}"
    if action == "PROMOTE":
        st.success(text)
    elif action in {"QUARANTINE", "REJECT"}:
        st.warning(text)
    elif action:
        st.info(text)
    smoke_leader = decision.get("top_smoke_candidate")
    if smoke_leader:
        st.caption(f"Smoke leader: `{smoke_leader}`")


@st.fragment(run_every=timedelta(seconds=2))
def crypto_smoke_progress_panel(repo: Path) -> None:
    proc = st.session_state.get("wb_crypto_smoke_proc")
    if proc is not None and getattr(proc, "poll", lambda: None)() is not None:
        st.session_state.wb_crypto_smoke_proc = None
        st.rerun()

    registry = _crypto_registry_snapshot(repo)
    status = _read_json(latest_status_path(repo))
    if status:
        _status_metrics(status, registry)
        left, right = st.columns([1, 1])
        with left:
            st.subheader("Stages")
            _render_stages(status)
        with right:
            st.subheader("Decision")
            _render_decision(status)
            if status.get("active_candidate"):
                st.caption(f"Active candidate: `{status['active_candidate']}`")
        st.subheader("Candidate Evidence")
        _render_candidates(status)
        smoke_triage_order = status.get("smoke_triage_order") or status.get("ranking")
        if smoke_triage_order:
            with st.expander("Smoke triage order"):
                st.dataframe(pd.DataFrame(smoke_triage_order), width="stretch", hide_index=True)
    else:
        st.info("No autonomous crypto run observed yet.")

    reports = _latest_crypto_reports(repo)
    if reports:
        with st.expander("Latest crypto smoke reports"):
            st.dataframe(pd.DataFrame(reports), width="stretch", hide_index=True)


def render_autonomous_panel(repo: Path) -> None:
    st.header("Autonomous Pipeline Monitor")
    render_crypto_run_controls(repo)
    crypto_smoke_progress_panel(repo)


def render_crypto_run_controls(repo: Path) -> None:
    registry = _crypto_registry_snapshot(repo)
    proc = st.session_state.get("wb_crypto_smoke_proc")
    running = proc is not None and getattr(proc, "poll", lambda: None)() is None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Crypto hypotheses", len(registry.get("hypotheses", [])))
    c2.metric("Crypto candidates", len(registry.get("candidates", [])))
    c3.metric("Backtest configs", len(registry.get("backtests", [])))
    c4.metric("Run smoke reports", len(_latest_crypto_reports(repo)))
    if not registry.get("ok", False):
        st.error(f"Crypto registry error: {registry.get('error', 'unknown')}")
    st.caption(
        "Crypto execution evidence is venue submit-to-ack. The Bitcoin node supplies mempool/blockspace "
        "features and point-in-time timing evidence; it is not an exchange order acknowledgement source."
    )

    disabled = running or not registry.get("candidates")
    if st.button("Run All Crypto Candidates", disabled=disabled, type="primary"):
        st.session_state.wb_crypto_smoke_proc = _start_crypto_smoke(repo, "")
        st.session_state.wb_crypto_smoke_started = datetime.now(timezone.utc).isoformat()
        st.rerun()

    if running:
        st.status("Crypto candidate loop running", state="running")
    elif proc is not None:
        code = proc.poll()
        st.session_state.wb_crypto_smoke_proc = None
        if code == 0:
            st.success("Crypto candidate loop finished.")
        else:
            st.warning(f"Crypto candidate loop exited with code {code}.")
