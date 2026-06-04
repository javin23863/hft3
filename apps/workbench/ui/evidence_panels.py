"""Workbench tab renderers backed by a normalized run evidence snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from workbench.src.run.evidence_snapshot import RunEvidenceSnapshot, read_json, read_text


def _df(rows: list[dict[str, Any]]) -> None:
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No observed rows for this stage.")


def _json_expander(label: str, payload: Any) -> None:
    if payload:
        with st.expander(label):
            st.json(payload)


def _chart_frame(rows: list[dict[str, Any]], index: str, columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    keep = [c for c in [index, *columns] if c in frame.columns]
    if index not in keep:
        return pd.DataFrame()
    frame = frame[keep].copy()
    for col in columns:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.set_index(index)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_bps(value: Any, *, decimals: int = 2) -> str:
    return f"{_num(value):,.{decimals}f} bps"


def _fmt_pct(value: Any) -> str:
    raw = _num(value)
    if abs(raw) <= 1.0:
        raw *= 100.0
    return f"{raw:,.1f}%"


def _short_id(value: Any, *, keep: int = 30) -> str:
    text = str(value or "—")
    return text if len(text) <= keep else f"{text[: keep - 1]}…"


def _candidate_label(value: Any) -> str:
    text = str(value or "unknown")
    return text.removeprefix("crypto_")


def _strategy_label(value: Any) -> str:
    words = _candidate_label(value).split("_")
    if not words:
        return "unknown"
    words[0] = words[0].upper()
    return " ".join(words[:2])


def _ack_display_row(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "UNKNOWN")
    reason = ""
    if "(" in status and status.endswith(")"):
        state, raw_reason = status.split("(", 1)
        status = state.strip()
        reason = raw_reason[:-1]
    reason_lc = reason.lower()
    if "submit" in reason_lc and "ack" in reason_lc and "not measured" in reason_lc:
        reason = "venue ack pairs missing"
        if "bitcoin" in reason_lc or "btc" in reason_lc:
            reason += "; BTC state packets observed"
    else:
        reason = reason.replace("crypto venue submit-to-ack pairs not measured", "venue ack pairs missing")
        reason = reason.replace("; Bitcoin node evidence", "; BTC state packets")
    return {
        "candidate": _candidate_label(row.get("candidate_id")),
        "scope": str(row.get("scope") or "").replace("crypto_venue_submit_ack", "venue submit→ack"),
        "measured": "yes" if row.get("measured") else "no",
        "state": status,
        "reason": reason or "—",
        "btc_node": str(row.get("btc_node_scope") or "").replace(
            "mempool/blockspace point-in-time",
            "mempool/PIT",
        ),
    }


def _display_df(rows: list[dict[str, Any]], columns: dict[str, str]) -> None:
    if not rows:
        st.info("No observed rows for this stage.")
        return
    frame = pd.DataFrame(rows)
    keep = [col for col in columns if col in frame.columns]
    if keep:
        frame = frame[keep].rename(columns=columns)
    st.dataframe(frame, width="stretch", hide_index=True)


def _leaderboard_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for col in (
        "proxy_net_pnl_bps",
        "proxy_trades",
        "proxy_hit_rate",
        "proxy_profit_factor",
        "proxy_max_drawdown_bps",
        "proxy_sharpe",
        "oos_ic",
    ):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "candidate_id" in frame.columns:
        frame["candidate_label"] = frame["candidate_id"].map(_candidate_label)
    return frame


def _altair_chart(chart: alt.Chart) -> None:
    st.altair_chart(
        chart.properties(width="container", height=260).configure_axis(labelLimit=150)
    )


def render_run_header(snapshot: RunEvidenceSnapshot) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Source", snapshot.source)
    c2.metric("Run", snapshot.run_id or "none")
    c3.metric("State", snapshot.state.upper())
    c4.metric("Stage", snapshot.current_stage or "—")
    if snapshot.root:
        st.caption(f"Artifact root: `{snapshot.root}`")


def render_autonomous_run(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Autonomous Run")
    render_run_header(snapshot)
    _df(snapshot.stages)
    if snapshot.artifacts:
        with st.expander("Artifact links", expanded=True):
            _df([{"artifact": k, "path": v} for k, v in snapshot.artifacts.items()])


def render_registry_data(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Registry & Data")
    render_run_header(snapshot)
    registry = snapshot.registry or {}
    data = snapshot.data or {}
    edge_packets = data.get("bitcoin_edge_packets") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Registry items", len(registry.get("candidates", [])) or len(registry))
    c2.metric("Data files", len(data.get("data_files", [])))
    c3.metric("Missing blockers", len(data.get("missing", [])))
    c4.metric("BTC state packets", edge_packets.get("status", "unknown"))
    feature_rows = (snapshot.diagnostics or {}).get("feature_rows") or []
    if feature_rows:
        st.subheader("Candidate registry")
        _display_df(
            feature_rows,
            {
                "candidate_id": "Candidate",
                "hypothesis_id": "Hypothesis",
                "target": "Research target",
                "btc_node_required": "BTC node",
                "features": "Feature inputs",
            },
        )
    elif registry.get("candidate_paths"):
        st.subheader("Candidate registry")
        _df([{"candidate": c, "path": p} for c, p in zip(registry.get("candidates", []), registry.get("candidate_paths", []))])
    elif registry:
        _json_expander("Registry", registry)
    if data.get("data_files"):
        st.subheader("Data availability")
        _df(data["data_files"])
    if data.get("missing"):
        st.subheader("Data blockers")
        _df(data["missing"])
    _json_expander("Symbol / universe", data.get("universe") or data.get("periods"))
    _json_expander("Bitcoin node / mempool evidence", data.get("btc_node"))
    _json_expander("Bitcoin state packet transport", edge_packets)


def render_backtest_evidence(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Backtest & P&L Evidence")
    rows = snapshot.backtest.get("rows") or []
    leaderboard = snapshot.backtest.get("proxy_leaderboard") or []
    equity_curves = snapshot.backtest.get("equity_curves") or {}
    holdout_rows = snapshot.backtest.get("holdout_stage_rows") or []
    control_rows = snapshot.backtest.get("negative_control_rows") or []

    if leaderboard:
        lb = _leaderboard_frame(leaderboard)
        c1, c2, c3, c4 = st.columns(4)
        top = leaderboard[0]
        c1.metric(
            "Top research P&L",
            _fmt_bps(top.get("proxy_net_pnl_bps"), decimals=0),
            help="Diagnostic P&L built from purged walk-forward out-of-sample rows.",
        )
        c2.metric("P&L leader", _strategy_label(top.get("candidate_id")))
        c3.metric("OOS trades", int(_num(top.get("proxy_trades"))))
        c4.metric("Worst drawdown", _fmt_bps(top.get("proxy_max_drawdown_bps")))
        st.caption("Diagnostic only: proxy P&L is not venue fills and is not a promotion gate.")
        if not lb.empty:
            st.subheader("P&L ranking")
            pnl_chart = (
                alt.Chart(lb)
                .mark_bar()
                .encode(
                    y=alt.Y("candidate_label:N", sort="-x", title="Candidate"),
                    x=alt.X("proxy_net_pnl_bps:Q", title="Net P&L proxy (bps)"),
                    color=alt.condition(
                        "datum.proxy_net_pnl_bps >= 0",
                        alt.value("#22c55e"),
                        alt.value("#ef4444"),
                    ),
                    tooltip=[
                        alt.Tooltip("candidate_id:N", title="Candidate"),
                        alt.Tooltip("target:N", title="Target"),
                        alt.Tooltip("proxy_net_pnl_bps:Q", title="Net bps", format=",.2f"),
                        alt.Tooltip("proxy_max_drawdown_bps:Q", title="Drawdown bps", format=",.2f"),
                        alt.Tooltip("proxy_trades:Q", title="Trades", format=","),
                    ],
                )
            )
            _altair_chart(pnl_chart)
            st.subheader("Edge quality")
            quality_chart = (
                alt.Chart(lb)
                .mark_circle(size=120, opacity=0.85)
                .encode(
                    x=alt.X("oos_ic:Q", title="OOS information coefficient"),
                    y=alt.Y("proxy_net_pnl_bps:Q", title="Net P&L proxy (bps)"),
                    size=alt.Size("proxy_trades:Q", title="OOS trades"),
                    color=alt.Color("proxy_sharpe:Q", title="Sharpe proxy"),
                    tooltip=[
                        alt.Tooltip("candidate_id:N", title="Candidate"),
                        alt.Tooltip("oos_ic:Q", title="OOS IC", format=".4f"),
                        alt.Tooltip("proxy_hit_rate:Q", title="Hit rate", format=".1%"),
                        alt.Tooltip("proxy_profit_factor:Q", title="Profit factor", format=".2f"),
                        alt.Tooltip("proxy_sharpe:Q", title="Sharpe proxy", format=".2f"),
                    ],
                )
            )
            _altair_chart(quality_chart)
            st.subheader("Candidate scorecard")
            scorecard = lb.to_dict("records")
            _display_df(
                scorecard,
                {
                    "candidate_id": "Candidate",
                    "target": "Target",
                    "oos_ic": "OOS IC",
                    "proxy_net_pnl_bps": "Net bps",
                    "proxy_trades": "Trades",
                    "proxy_hit_rate": "Hit rate",
                    "proxy_profit_factor": "Profit factor",
                    "proxy_max_drawdown_bps": "Drawdown bps",
                    "proxy_sharpe": "Sharpe proxy",
                    "proxy_status": "Evidence",
                },
            )

    equity_candidates = [c for c, curve in equity_curves.items() if curve]
    if equity_candidates:
        selected = st.selectbox(
            "OOS equity candidate",
            equity_candidates,
            key=f"equity_curve_candidate_{snapshot.source}_{snapshot.run_id}",
        )
        curve = pd.DataFrame(equity_curves.get(selected) or [])
        if not curve.empty and "step" in curve.columns:
            curve = curve.copy()
            curve["step"] = pd.to_numeric(curve["step"], errors="coerce")
            for col in ("equity_bps", "net_pnl_bps", "return_proxy_bps"):
                if col in curve.columns:
                    curve[col] = pd.to_numeric(curve[col], errors="coerce")
            st.subheader("OOS equity curve")
            if "equity_bps" in curve.columns:
                equity_chart = (
                    alt.Chart(curve)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("step:Q", title="OOS step"),
                        y=alt.Y("equity_bps:Q", title="Equity proxy (bps)"),
                        tooltip=[
                            alt.Tooltip("fold_id:N", title="Fold"),
                            alt.Tooltip("step:Q", title="Step"),
                            alt.Tooltip("position:Q", title="Position"),
                            alt.Tooltip("net_pnl_bps:Q", title="Net bps", format=",.2f"),
                            alt.Tooltip("equity_bps:Q", title="Equity bps", format=",.2f"),
                        ],
                    )
                )
                _altair_chart(equity_chart)
            trade_cols = [
                c for c in (
                    "fold_id",
                    "prediction",
                    "position",
                    "return_proxy_bps",
                    "net_pnl_bps",
                    "equity_bps",
                )
                if c in curve.columns
            ]
            st.subheader("Recent OOS trade tape")
            _display_df(
                curve[trade_cols].tail(25).to_dict("records"),
                {
                    "fold_id": "Fold",
                    "prediction": "Prediction",
                    "position": "Position",
                    "return_proxy_bps": "Return proxy bps",
                    "net_pnl_bps": "Net bps",
                    "equity_bps": "Equity bps",
                },
            )

    st.subheader("Backtest rows")
    _display_df(
        rows,
        {
            "candidate_id": "Candidate",
            "hypothesis_id": "Hypothesis",
            "target": "Target",
            "pass_fail": "Research gate",
            "oos_ic": "OOS IC",
            "rows": "Rows",
            "folds": "Folds",
            "holdout": "Holdout",
            "proxy_net_pnl_bps": "Net bps",
            "proxy_trades": "Trades",
            "proxy_max_drawdown_bps": "Drawdown bps",
            "proxy_sharpe": "Sharpe proxy",
        },
    )
    if holdout_rows:
        st.subheader("Holdout IC by stage")
        holdout_frame = pd.DataFrame(holdout_rows)
        if not holdout_frame.empty:
            _display_df(
                holdout_rows,
                {
                    "candidate_id": "Candidate",
                    "stage": "Stage",
                    "mode": "Mode",
                    "ic": "IC",
                    "n_rows": "Rows",
                    "status": "Status",
                },
            )
            chart = holdout_frame.pivot_table(
                index="candidate_id",
                columns="stage",
                values="ic",
                aggfunc="first",
            )
            st.bar_chart(chart)
    if control_rows:
        st.subheader("Leakage controls")
        _display_df(
            control_rows,
            {
                "candidate_id": "Candidate",
                "real_oos_ic": "Real OOS IC",
                "shuffled_labels_ic": "Shuffled labels IC",
                "shifted_features_ic": "Shifted features IC",
                "shuffled_degraded": "Labels degraded",
                "shifted_degraded": "Features degraded",
            },
        )
        control_frame = _chart_frame(
            control_rows,
            "candidate_id",
            ["real_oos_ic", "shuffled_labels_ic", "shifted_features_ic"],
        )
        if not control_frame.empty:
            st.bar_chart(control_frame)
    _json_expander("Backtest summary", snapshot.backtest.get("summary"))
    _json_expander("Raw backtest metrics", {k: v for k, v in snapshot.backtest.items() if k != "rows"})


def render_latency_evidence(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Execution & Latency Evidence")
    latency = snapshot.latency or {}
    rows = latency.get("execution_ack_rows") or []
    if rows:
        measured = sum(1 for row in rows if row.get("measured"))
        blocked = sum(1 for row in rows if str(row.get("status", "")).upper().startswith("INSUFFICIENT"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Submit→ack measured", measured)
        c2.metric("Execution blockers", blocked)
        c3.metric("Candidates checked", len(rows))
        st.caption("Crypto readiness uses venue submit-to-ack evidence; Bitcoin node packets are market-state evidence.")
        _display_df(
            [_ack_display_row(row) for row in rows],
            {
                "candidate": "Candidate",
                "scope": "Scope",
                "measured": "Measured",
                "state": "Ack state",
                "reason": "Missing evidence",
                "btc_node": "BTC node",
            },
        )
    elif latency.get("latest_event_diagnostics"):
        diag = latency["latest_event_diagnostics"]
        _df([
            {
                "metric": "measured_production_p99_us",
                "value": diag.get("measured_production_p99_us"),
            },
            {
                "metric": "survives_cpp_execution_delay",
                "value": diag.get("survives_cpp_execution_delay"),
            },
            {
                "metric": "latency_profitability_buffer_us",
                "value": diag.get("latency_profitability_buffer_us"),
            },
        ])
    else:
        st.info("No observed latency evidence for this run.")
    _json_expander("Venue profiles", latency.get("venue_profiles"))
    _json_expander("Bitcoin node latency/PIT timing", latency.get("node_profile"))
    history = latency.get("edge_packet_history") or []
    if history:
        st.subheader("Bitcoin state packet stream")
        hist = pd.DataFrame(history)
        if not hist.empty and "sequence_number" in hist.columns:
            for col in (
                "sequence_number",
                "wire_bytes",
                "mempool_tx_count",
                "mempool_bytes",
                "fee_mean_sat_vb",
                "blockspace_stress_score",
                "delta_count",
                "sequence_gap_count",
            ):
                if col in hist.columns:
                    hist[col] = pd.to_numeric(hist[col], errors="coerce")
            hist = hist.reset_index(drop=True)
            hist["packet_index"] = hist.index + 1
            latest = hist.iloc[-1].to_dict()
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Packets", len(hist))
            c2.metric("Latest seq", int(_num(latest.get("sequence_number"))))
            c3.metric("Mempool tx", int(_num(latest.get("mempool_tx_count"))))
            c4.metric("Wire bytes", int(_num(latest.get("wire_bytes"))))
            c5.metric("Seq gaps", int(_num(latest.get("sequence_gap_count"))))
            pressure_cols = [
                col for col in ("mempool_tx_count", "delta_count") if col in hist.columns
            ]
            if pressure_cols:
                pressure = hist[["packet_index", "sequence_number", *pressure_cols]].melt(
                    id_vars=["packet_index", "sequence_number"],
                    var_name="metric",
                    value_name="value",
                )
                pressure_chart = (
                    alt.Chart(pressure)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("packet_index:Q", title="Packet"),
                        y=alt.Y("value:Q", title="Count"),
                        color=alt.Color("metric:N", title="Metric"),
                        tooltip=[
                            alt.Tooltip("packet_index:Q", title="Packet"),
                            alt.Tooltip("sequence_number:Q", title="Sequence"),
                            alt.Tooltip("metric:N", title="Metric"),
                            alt.Tooltip("value:Q", title="Value", format=","),
                        ],
                    )
                )
                _altair_chart(pressure_chart)
            transport_cols = [
                col
                for col in ("wire_bytes", "fee_mean_sat_vb", "blockspace_stress_score")
                if col in hist.columns
            ]
            if transport_cols:
                transport = hist[["packet_index", "sequence_number", *transport_cols]].melt(
                    id_vars=["packet_index", "sequence_number"],
                    var_name="metric",
                    value_name="value",
                )
                transport_chart = (
                    alt.Chart(transport)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("packet_index:Q", title="Packet"),
                        y=alt.Y("value:Q", title="Value"),
                        color=alt.Color("metric:N", title="Metric"),
                        tooltip=[
                            alt.Tooltip("packet_index:Q", title="Packet"),
                            alt.Tooltip("sequence_number:Q", title="Sequence"),
                            alt.Tooltip("metric:N", title="Metric"),
                            alt.Tooltip("value:Q", title="Value", format=",.2f"),
                        ],
                    )
                )
                _altair_chart(transport_chart)
    _json_expander("Bitcoin state packet transport", latency.get("bitcoin_edge_packets"))
    latency_profile = latency.get("cpp_latency_profile") or latency.get("latency_profile")
    if latency_profile:
        profile_label = "Execution latency profile"
        _json_expander(profile_label, latency_profile)


def render_signal_diagnostics(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Signal Diagnostics")
    diagnostics = snapshot.diagnostics or {}
    if diagnostics.get("feature_rows"):
        st.subheader("Feature map")
        _display_df(
            diagnostics["feature_rows"],
            {
                "candidate_id": "Candidate",
                "hypothesis_id": "Hypothesis",
                "target": "Target",
                "features": "Inputs",
                "btc_node_required": "BTC node",
                "ablation": "Ablation",
            },
        )
    _json_expander("Feature lineage", diagnostics.get("feature_lineage"))
    _json_expander("Model combinations", diagnostics.get("model_combination"))
    _json_expander("Composition", diagnostics.get("composition"))
    _json_expander("Latest event diagnostics", diagnostics.get("latest_event_diagnostics"))
    if diagnostics.get("edge_packet_schema"):
        st.subheader("Bitcoin edge packet schema")
        _df(diagnostics["edge_packet_schema"])
    if diagnostics.get("feature_builders"):
        st.subheader("Feature builders")
        _df([{"path": p} for p in diagnostics["feature_builders"]])
    if diagnostics.get("align_modules"):
        st.subheader("Alignment / PIT modules")
        _df([{"path": p} for p in diagnostics["align_modules"]])


def render_robustness(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Walk-Forward & Robustness")
    robustness = snapshot.robustness or {}
    if robustness.get("rows"):
        rows = robustness["rows"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Candidates", len(rows))
        c2.metric("Holdout passes", sum(1 for r in rows if str(r.get("holdout", "")).lower() == "pass"))
        c3.metric("Purged CV", sum(1 for r in rows if r.get("purged_cv")))
        _display_df(
            rows,
            {
                "candidate_id": "Candidate",
                "purged_cv": "Purged CV",
                "purged_splits": "Splits",
                "holdout": "Holdout",
                "shuffled_degraded": "Labels degraded",
                "shifted_degraded": "Features degraded",
                "randomized_degraded": "Randomized degraded",
            },
        )
    _json_expander("Robustness gates", robustness.get("gates"))
    _json_expander("Walk-forward", robustness.get("walk_forward"))
    _json_expander("Walk-forward correlation", robustness.get("wfc"))
    _json_expander("Robustness checks", robustness.get("robustness_checks"))
    if robustness.get("pending"):
        st.warning("Pending robustness checks: " + ", ".join(map(str, robustness["pending"])))
    if robustness.get("failed"):
        st.error("Failed robustness checks: " + ", ".join(map(str, robustness["failed"])))


def render_decision_registry(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Decision & Registry")
    decision = snapshot.decision or {}
    action = str(decision.get("action") or decision.get("decision") or "UNKNOWN")
    reason = str(decision.get("reason") or "")
    if action.upper() == "PROMOTE":
        st.success(f"{action}: {reason}")
    elif action.upper() in {"QUARANTINE", "REJECT"}:
        st.warning(f"{action}: {reason}")
    else:
        st.info(f"{action}: {reason}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Top research candidate", _short_id(decision.get("top_research_candidate"), keep=24))
    c2.metric("Research passes", int(_num(decision.get("research_pass_count"))))
    c3.metric("Live-ready registry", bool(decision.get("live_registry_ready")))
    ranking = decision.get("ranking") or []
    if ranking:
        st.subheader("Candidate ranking")
        _df(ranking)
    _json_expander("Scoring summary", decision.get("scoring_summary"))
    blockers = decision.get("blocking_gates") or []
    if blockers:
        st.subheader("Blocking gates")
        _df(blockers)
    if decision.get("bitcoin_edge_packet_status"):
        st.caption(f"Bitcoin state packet gate: {decision['bitcoin_edge_packet_status']}")


def render_reports_analyst(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Reports & Analyst")
    reports = snapshot.reports or {}
    paths = []
    for key, value in reports.items():
        if isinstance(value, list):
            paths.extend({"artifact": key, "path": p} for p in value if p)
        elif value:
            paths.append({"artifact": key, "path": value})
    _df(paths)
    for preferred in ("report_md", "latest_report", "after_action_report"):
        path = Path(str(reports.get(preferred, "")))
        text = read_text(path)
        if text:
            st.markdown(text)
            break


def render_system(snapshot: RunEvidenceSnapshot, repo: Path) -> None:
    st.header("System")
    _json_expander("Run system payload", snapshot.system)
    _json_expander("Bitcoin edge packet system state", (snapshot.system or {}).get("bitcoin_edge_packets"))
    validation_dir = repo / "runtime" / "validation"
    rows = []
    for name in (
        "certification_registry.json",
        "fast_gate_report.json",
        "backtester_certification_scorecard.json",
        "champion_promotion_gate_report.json",
    ):
        path = validation_dir / name
        rows.append({"artifact": name, "exists": path.is_file(), "path": str(path)})
    _df(rows)
    for row in rows:
        if row["exists"]:
            _json_expander(row["artifact"], read_json(Path(row["path"])))
