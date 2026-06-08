"""Equities lane panel: low-float parabolic anomaly stock sessions."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()
from equities_lane.src.types import DecadalSession


def _load_decadal_sessions(repo: Path) -> list[dict]:
    import yaml
    cfg = repo / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    out = []
    for s in raw.get("sessions", []):
        if s.get("skip_pull"):
            continue
        sid = s["id"]
        ndjson = repo / "data" / "equities" / "normalized" / f"{s['symbol']}_{s['date']}.ndjson"
        has_data = ndjson.is_file()
        out.append({
            "id": sid,
            "symbol": s["symbol"],
            "date": s["date"],
            "catalyst": s.get("catalyst", ""),
            "dataset": s.get("dataset", ""),
            "has_data": has_data,
            "ndjson_path": str(ndjson) if has_data else "",
        })
    return sorted(out, key=lambda x: x["date"])


def _run_backtest(repo: Path, session_path: str, ablation: str | None = None) -> dict[str, Any]:
    env = dict(sys.modules["os"].environ)
    from hft3_bootstrap import pythonpath_entries
    env["PYTHONPATH"] = sys.modules["os"].pathsep.join(pythonpath_entries(repo))

    cmd = [
        sys.executable, str(repo / "scripts" / "run_stocks_lane.py"),
        "--session-path", session_path,
        "--max-events", "50000",
    ]
    if ablation:
        cmd.extend(["--ablation", ablation])

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, timeout=120)
    elapsed = time.time() - t0

    pnl = 0.0
    trades = 0
    for line in proc.stdout.splitlines():
        if "pnl=" in line.lower() or "backtest:" in line:
            import re
            m = re.search(r"pnl=([-\d.]+)", line)
            if m:
                pnl = float(m.group(1))
            m2 = re.search(r"trades?=(\d+)", line)
            if m2:
                trades = int(m2.group(1))

    return {
        "rc": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "pnl": pnl,
        "trades": trades,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-500:] if proc.stderr else "",
    }


def equities_panel(repo: Path) -> None:
    st.subheader("Low-Float Parabolic Anomaly Sessions")

    sessions = _load_decadal_sessions(repo)
    if not sessions:
        st.info("No equities session data found. Run pull_equities_decadal.ps1 first.")
        return

    ready = sum(1 for s in sessions if s["has_data"])
    st.caption(f"{ready} / {len(sessions)} sessions ready")

    rows = []
    for s in sessions:
        rows.append({
            "Symbol": s["symbol"],
            "Date": s["date"],
            "Catalyst": s["catalyst"],
            "Data": "ready" if s["has_data"] else "missing",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=35 * len(rows) + 38)

    symbols = [s["symbol"] for s in sessions if s["has_data"]]
    if not symbols:
        st.warning("No sessions have normalized data. Run `python scripts/run_stocks_lane.py --discover` to check.")
        return

    c_sym, c_run = st.columns([2, 1])
    with c_sym:
        symbol = st.selectbox("Session", symbols, key="wb__eq_sym")
    with c_run:
        do_run = st.button("Run Backtest", key="wb__eq_run", type="primary", use_container_width=True)

    if do_run and symbol:
        session = next(s for s in sessions if s["symbol"] == symbol and s["has_data"])
        with st.spinner(f"Running {symbol} backtest..."):
            result = _run_backtest(repo, session["ndjson_path"])
        if result["rc"] == 0:
            st.success(f"{symbol}: PnL={result['pnl']:.2f}  trades={result['trades']}  wall={result['elapsed_s']}s")
        else:
            st.error(f"{symbol} failed (rc={result['rc']})")
        with st.expander("stdout"):
            st.code(result["stdout"])
        with st.expander("stderr"):
            st.code(result["stderr"] or "(none)")

    if st.button("Run All 13 Sessions", key="wb__eq_run_all"):
        results = []
        progress = st.progress(0, "Running sessions...")
        for i, s in enumerate(sessions):
            if not s["has_data"]:
                continue
            progress.progress((i + 1) / len(sessions), f"{s['symbol']}...")
            r = _run_backtest(repo, s["ndjson_path"])
            results.append({"symbol": s["symbol"], **r})
        progress.empty()

        df = pd.DataFrame(results)
        if not df.empty:
            st.subheader("All Results")
            st.dataframe(df[["symbol", "pnl", "trades", "elapsed_s", "rc"]], use_container_width=True, hide_index=True)
            st.metric("Total PnL", f"{df['pnl'].sum():.2f}")
            st.metric("Total Trades", int(df["trades"].sum()))
        else:
            st.warning("No sessions had data to run.")
