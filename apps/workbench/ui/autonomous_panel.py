"""Streamlit panel: autonomous campaign controls + status.

Buttons (in this exact order so they read as a natural workflow):
  1. Start Full Autonomous Run
  2. Pause
  3. Resume
  4. Stop
  5. Reset Campaign
  6. Open Active Run Folder / Show Artifact Path
  7. Refresh Evidence

The panel reads the active campaign's evidence_snapshot.json, summary.json,
and metrics.json — never stale latest files. The active campaign_id is
held in st.session_state.wb_autonomous_cid so refresh + reset are local
to one campaign.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from hft3_bootstrap import setup_repo_paths

from workbench.src.artifacts.paths import campaign_dir, workbench_runs_dir
from workbench.src.run.evidence_snapshot import is_fresh, read_evidence_or_summary


def _git_sha(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _backend_pid(artifact_dir: Path) -> Optional[int]:
    """Return the PID of the running campaign subprocess, if any."""
    proc = st.session_state.get("wb_autonomous_proc")
    if proc is None:
        return None
    poll = getattr(proc, "poll", None)
    if poll is None:
        return None
    rc = poll()
    if rc is None:
        return proc.pid
    return None


def _write_control(repo: Path, campaign_id: str, command: str) -> None:
    d = campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "control.json").write_text(json.dumps({"command": command}), encoding="utf-8")


def _reset_campaign_local(repo: Path, campaign_id: str) -> None:
    """Local reset: mark state as reset so the UI does not pick it up again.
    Does NOT delete artifacts on disk; that would destroy evidence.
    """
    if not campaign_id:
        return
    d = campaign_dir(campaign_id)
    status = d / "status.json"
    if status.is_file():
        try:
            payload = json.loads(status.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        payload["state"] = "reset"
        payload["reset_at"] = time.time()
        status.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def init_session() -> None:
    if "wb_autonomous_cid" not in st.session_state:
        st.session_state.wb_autonomous_cid = ""
    if "wb_autonomous_proc" not in st.session_state:
        st.session_state.wb_autonomous_proc = None
    if "wb_autonomous_symbols" not in st.session_state:
        st.session_state.wb_autonomous_symbols = ["MES.v.0"]
    if "wb_autonomous_mode" not in st.session_state:
        st.session_state.wb_autonomous_mode = "full"  # full | smoke
    if "wb_autonomous_kinds" not in st.session_state:
        st.session_state.wb_autonomous_kinds = []
    if "wb_autonomous_job_filter" not in st.session_state:
        st.session_state.wb_autonomous_job_filter = ""


def _start_run(repo: Path, *, symbols: List[str], smoke: bool, kinds: List[str], job_filter: str) -> str:
    """Spawn the autonomous run as a child python process. Returns campaign_id."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    campaign_id = f"autonomous_{ts}"
    cmd = [sys.executable, "-m", "workbench", "autonomous", "--symbol", *symbols, "--campaign-id", campaign_id]
    if smoke:
        cmd.append("--trial")
    if kinds:
        cmd.extend(["--include-kinds", *kinds])
    if job_filter.strip():
        cmd.extend(["--job-filter", *[m.strip() for m in job_filter.split(",") if m.strip()]])
    env = os.environ.copy()
    from hft3_bootstrap import pythonpath_entries

    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries(repo))
    log = repo / "runtime" / "logs" / "workbench_launcher.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8")
    f.write(
        f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] autonomous start campaign_id={campaign_id} cmd={cmd}\n"
    )
    f.flush()
    proc = subprocess.Popen(cmd, cwd=str(repo), env=env, stdout=f, stderr=subprocess.STDOUT)
    st.session_state.wb_autonomous_proc = proc
    st.session_state.wb_autonomous_cid = campaign_id
    return campaign_id


def _format_elapsed(seconds: float) -> str:
    if seconds < 0:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@st.fragment(run_every=timedelta(seconds=2))
def autonomous_panel(repo: Path) -> None:
    init_session()

    cid = st.session_state.wb_autonomous_cid
    snap = read_evidence_or_summary(campaign_dir(cid)) if cid else {}
    state = str(snap.get("state", "")).lower() or ("idle" if not cid else "unknown")
    counts = snap.get("counts", {}) if isinstance(snap, dict) else {}

    # ---- Readout strip ---------------------------------------------------
    st.subheader("Autonomous run status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Repo", repo.name)
    c2.metric("Git SHA", _git_sha(repo)[:8] if _git_sha(repo) != "unknown" else "unknown")
    c3.metric("Campaign", cid or "—")
    c4.metric("State", state.upper())

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Lane", "workbench")
    c6.metric(
        "Model",
        (snap.get("current_job", {}) or {}).get("model_id", "—") if isinstance(snap, dict) else "—",
    )
    c7.metric(
        "Symbol/Event",
        (snap.get("current_job", {}) or {}).get("symbol", "—") if isinstance(snap, dict) else "—",
    )
    c8.metric("Stage", (snap.get("current_job", {}) or {}).get("phase", "—") if isinstance(snap, dict) else "—")

    c9, c10, c11, c12 = st.columns(4)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    blocked = counts.get("blocked", 0)
    skipped = counts.get("skipped", 0)
    pending = counts.get("pending", 0)
    cancelled = counts.get("cancelled", 0)
    c9.metric("Completed", completed)
    c10.metric("Failed", failed)
    c11.metric("Blocked", blocked)
    c12.metric("Skipped", skipped)
    c13, c14, c15, c16 = st.columns(4)
    c13.metric("Pending", pending)
    c14.metric("Cancelled", cancelled)
    if cid:
        base = campaign_dir(cid)
        snap_mtime = (base / "evidence_snapshot.json").stat().st_mtime if (base / "evidence_snapshot.json").is_file() else None
        elapsed = (time.time() - snap_mtime) if snap_mtime else 0
        c15.metric("Heartbeat age", _format_elapsed(elapsed))
        c16.metric("Backend PID", _backend_pid(base) or "—")
    else:
        c15.metric("Heartbeat age", "—")
        c16.metric("Backend PID", "—")

    last_artifact = snap.get("last_artifact", "") if isinstance(snap, dict) else ""
    control_command = snap.get("control_command", "run") if isinstance(snap, dict) else "run"
    c17, c18, c19 = st.columns(3)
    c17.caption(f"Last artifact: `{last_artifact or '—'}`")
    c18.caption(f"Control command: **{control_command}**")
    c19.caption(f"Active campaign folder: `{campaign_dir(cid) if cid else '—'}`")

    # ---- Controls --------------------------------------------------------
    st.subheader("Controls")
    symbols_str = st.text_input(
        "Symbols (space-separated)",
        value=" ".join(st.session_state.wb_autonomous_symbols),
        key="wb_autonomous_symbols_input",
    )
    kinds_str = st.text_input(
        "Model kinds filter (hypothesis structural hybrid — empty = all)",
        value=" ".join(st.session_state.wb_autonomous_kinds),
        key="wb_autonomous_kinds_input",
    )
    job_filter_str = st.text_input(
        "Model filter (comma-separated, empty = all)",
        value=st.session_state.wb_autonomous_job_filter,
        key="wb_autonomous_job_filter_input",
    )
    smoke = st.checkbox(
        "Smoke / trial mode (faster, allows partial NPZ)",
        value=st.session_state.wb_autonomous_mode == "smoke",
        key="wb_autonomous_smoke",
    )
    st.session_state.wb_autonomous_mode = "smoke" if smoke else "full"

    col_a, col_b, col_c, col_d, col_e, col_f, col_g = st.columns(7)
    with col_a:
        if st.button("Start Full Autonomous Run", type="primary", use_container_width=True, key="wb_auto_start"):
            syms = [s for s in symbols_str.split() if s]
            kinds = [k for k in kinds_str.split() if k]
            if not syms:
                st.error("At least one symbol is required.")
            else:
                st.session_state.wb_autonomous_symbols = syms
                st.session_state.wb_autonomous_kinds = kinds
                st.session_state.wb_autonomous_job_filter = job_filter_str
                cid2 = _start_run(
                    repo,
                    symbols=syms,
                    smoke=smoke,
                    kinds=kinds,
                    job_filter=job_filter_str,
                )
                st.success(f"Started {cid2}")
                st.rerun()
    with col_b:
        pause_disabled = not cid or state == "paused"
        if st.button("Pause", use_container_width=True, key="wb_auto_pause", disabled=pause_disabled):
            _write_control(repo, cid, "pause")
            st.toast("Pause requested")
    with col_c:
        resume_disabled = not cid or state != "paused"
        if st.button("Resume", use_container_width=True, key="wb_auto_resume", disabled=resume_disabled):
            _write_control(repo, cid, "run")
            st.toast("Resume requested")
    with col_d:
        stop_disabled = not cid or state in {"complete", "stopped", "reset", "cancelled", "fail", "blocked"}
        if st.button("Stop", use_container_width=True, key="wb_auto_stop", disabled=stop_disabled):
            _write_control(repo, cid, "stop")
            st.toast("Stop requested")
    with col_e:
        if st.button("Reset Campaign", use_container_width=True, key="wb_auto_reset"):
            if cid:
                _reset_campaign_local(repo, cid)
                proc = st.session_state.get("wb_autonomous_proc")
                if proc is not None and getattr(proc, "poll", None) and proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                st.session_state.wb_autonomous_cid = ""
                st.session_state.wb_autonomous_proc = None
                st.toast("Campaign reset (artifacts preserved on disk)")
                st.rerun()
    with col_f:
        if st.button("Open Active Run Folder", use_container_width=True, key="wb_auto_open", disabled=not cid):
            path = str(campaign_dir(cid))
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            st.toast(f"Opened {path}")
    with col_g:
        if st.button("Refresh Evidence", use_container_width=True, key="wb_auto_refresh"):
            st.rerun()

    # ---- Honest metrics block ------------------------------------------
    if cid:
        st.subheader("Trader metrics (active campaign)")
        metrics_path = campaign_dir(cid) / "metrics.json"
        if metrics_path.is_file():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metrics = {}
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Total PnL", f"{float(metrics.get('total_pnl') or 0):.2f}")
            npf = metrics.get("net_pnl_after_fees")
            mc2.metric("Net PnL (after fees)", f"{float(npf):.2f}" if npf is not None else "MISSING")
            mc3.metric("Trades", int(metrics.get("num_trades") or 0))
            mc4.metric("Expectancy / trade", f"{float(metrics.get('expectancy_per_trade') or 0):.2f}")
            mc5, mc6, mc7, mc8 = st.columns(4)
            for label, key, col in (
                ("Win rate", "win_rate", mc5),
                ("Profit factor", "profit_factor", mc6),
                ("Sharpe", "sharpe_ratio", mc7),
                ("Sortino", "sortino_ratio", mc8),
            ):
                v = metrics.get(key)
                col.metric(label, f"{float(v):.3f}" if isinstance(v, (int, float)) else "MISSING")
            mc9, mc10, mc11, mc12 = st.columns(4)
            for label, key, col in (
                ("Max DD", "max_drawdown", mc9),
                ("Calmar", "calmar_ratio", mc10),
                ("Latency p99 (ms)", "latency_p99_ms", mc11),
                ("Coverage %", "data_coverage_pct", mc12),
            ):
                v = metrics.get(key)
                col.metric(label, f"{float(v):.3f}" if isinstance(v, (int, float)) else "MISSING")
            missing = metrics.get("missing_required") or []
            if missing:
                with st.expander(
                    f"Honest gap report: {len(missing)} metrics marked MISSING_REQUIRED_LEDGER",
                    expanded=False,
                ):
                    rows = [
                        {
                            "metric": m.get("metric"),
                            "required_input": m.get("required_input"),
                            "status": m.get("status"),
                        }
                        for m in missing
                    ]
                    import pandas as pd

                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("metrics.json not yet written — runner is still in early state.")
