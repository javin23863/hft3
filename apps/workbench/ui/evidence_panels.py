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


def _latency_metric_row(metrics: dict[str, Any], metric: str, label: str, view: str) -> dict[str, Any]:
    payload = metrics.get(metric) or {}
    count = int(_num(payload.get("count")))

    def value(field: str) -> float | None:
        if count <= 0 or payload.get(field) is None:
            return None
        return _num(payload.get(field))

    return {
        "view": view,
        "metric": label,
        "count": count,
        "p50_us": value("p50_us"),
        "p90_us": value("p90_us"),
        "p95_us": value("p95_us"),
        "p99_us": value("p99_us"),
        "p99_9_us": value("p99_9_us"),
        "max_us": value("max_us"),
    }


def _latency_metric_label(metrics: dict[str, Any], metric: str, field: str = "p50_us") -> str:
    payload = metrics.get(metric) or {}
    count = int(_num(payload.get("count")))
    if count <= 0 or payload.get(field) is None:
        return "blocked"
    return f"{_num(payload.get(field)):,.1f} us"


def _envelope_metric_label(metric: dict[str, Any], field: str) -> str:
    if not isinstance(metric, dict) or metric.get(field) is None:
        return "blocked"
    count = metric.get("count")
    if count is not None and _num(count) <= 0 and metric.get("observed") is False:
        return "blocked"
    return f"{_num(metric.get(field)):,.1f} us"


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


def _first_blocker_reason(blockers: list[Any]) -> str:
    for blocker in blockers:
        if isinstance(blocker, dict):
            reason = blocker.get("reason") or blocker.get("error") or blocker.get("message")
            if reason:
                return str(reason)
            gate = blocker.get("gate")
            status = blocker.get("status")
            if gate or status:
                return " / ".join(str(part) for part in (gate, status) if part)
        elif blocker:
            return str(blocker)
    return ""


def _run_header_blocker_message(snapshot: RunEvidenceSnapshot) -> str:
    decision = snapshot.decision or {}
    blockers = decision.get("blocking_gates") or []
    if blockers:
        reason = _first_blocker_reason(blockers) or decision.get("reason") or snapshot.current_stage
        return f"Backend readiness blocked: {reason}"

    action = str(decision.get("action") or decision.get("status") or "").upper()
    reason = str(decision.get("reason") or decision.get("current_stage") or snapshot.current_stage or "").strip()
    if action == "BLOCKED" and not decision.get("live_registry_ready") and reason:
        return f"Backend readiness blocked: {action}: {reason}"
    return ""


def render_run_header(snapshot: RunEvidenceSnapshot) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Source", snapshot.source)
    c2.metric("Run", snapshot.run_id or "none")
    c3.metric("State", snapshot.state.upper())
    c4.metric("Stage", snapshot.current_stage or "—")
    if snapshot.root:
        st.caption(f"Artifact root: `{snapshot.root}`")
    blocker_message = _run_header_blocker_message(snapshot)
    if blocker_message:
        st.error(blocker_message)


def render_autonomous_run(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Autonomous Run")
    render_run_header(snapshot)
    loop = snapshot.self_learning_loop or {}
    loop_rows = loop.get("stages") or []
    if loop_rows:
        st.subheader("Self-Learning Loop")
        st.caption(
            "Parameter learning is controlled by config bounds, WFC matrix evidence, plateau selection, "
            "and robustness gates. GPT-5.5 after-action is analyst feedback only and cannot tune "
            "parameters or promote candidates."
        )
        _display_df(
            loop_rows,
            {
                "step": "Step",
                "status": "State",
                "meaning": "What it means",
            },
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Analyst LLM status", loop.get("llm_status", "missing"))
        c2.metric("Analyst can promote", bool(loop.get("llm_can_promote")))
        c3.metric("Relationship review only", bool(loop.get("relationship_review_only")))
    coverage = (snapshot.system or {}).get("pipeline_coverage") or []
    if coverage:
        st.subheader("Pipeline Evidence Coverage")
        st.caption("Observed means this selected run emitted the evidence. Present-not-wired means the repo has the code path, but this run did not attach its artifacts.")
        _display_df(
            coverage,
            {
                "stage": "Layer",
                "status": "Status",
                "role": "Role",
                "artifact_contract": "Artifact contract",
                "authority": "Authority",
            },
        )
        with st.expander("Coverage details"):
            _display_df(
                coverage,
                {
                    "stage": "Layer",
                    "evidence": "Evidence",
                    "reason": "Meaning",
                },
            )
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
    c3.metric("Missing data", len(data.get("missing", [])))
    c4.metric("BTC state packets", edge_packets.get("status", "unknown"))
    event_universe = registry.get("event_universe") or data.get("event_universe") or {}
    if event_universe:
        st.subheader("Economic Event Universe")
        if event_universe.get("status") == "BLOCKING":
            st.error(f"Event universe did not load cleanly: {event_universe.get('error', '')}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Universe defined", int(_num(event_universe.get("event_type_count"))))
        c2.metric("Calendar rows", int(_num(event_universe.get("calendar_row_count"))))
        c3.metric("Sourced runnable", int(_num(event_universe.get("runnable_event_count"))))
        c4.metric("Snapshot artifacts", int(_num(event_universe.get("snapshot_artifact_count"))))
        status_counts = event_universe.get("row_status_counts") or {}
        if status_counts:
            _display_df(
                [{"row_status": key, "count": value} for key, value in sorted(status_counts.items())],
                {"row_status": "Row status", "count": "Rows"},
            )
        types = event_universe.get("event_types") or []
        if types:
            _display_df(
                types,
                {
                    "event_type": "Event type",
                    "status": "Universe status",
                    "agency": "Agency",
                    "event_context": "Context",
                    "calendar_row_count": "Calendar rows",
                    "runnable_row_count": "Runnable rows",
                    "first_release_date": "First",
                    "last_release_date": "Last",
                },
            )
        calendar_rows = event_universe.get("calendar_rows") or []
        if calendar_rows:
            st.caption(
                "Full canonical calendar universe. SEED rows are visible for planning and data-cost coverage; "
                "SOURCED rows are the generated campaign-ready subset."
            )
            _display_df(
                calendar_rows,
                {
                    "event_type": "Type",
                    "release_date": "Date",
                    "row_status": "Row status",
                    "runnable_eligible": "Runnable",
                    "event_context": "Context",
                    "source_file": "Source file",
                },
            )
        with st.expander("Generated sourced campaign rows", expanded=True):
            _display_df(
                event_universe.get("runnable_events") or [],
                {
                    "event_id": "Event ID",
                    "event_type": "Type",
                    "release_date": "Date",
                    "event_context": "Context",
                    "row_status": "Row status",
                    "source_file": "Source file",
                },
            )
    rithmic_trial = data.get("rithmic_trial") or {}
    if rithmic_trial.get("observed"):
        st.subheader("Rithmic Paper Data")
        event_counts = rithmic_trial.get("event_type_counts") or {}
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Symbol", rithmic_trial.get("symbol", ""))
        c2.metric("Exchange", rithmic_trial.get("exchange", ""))
        c3.metric("Rows", int(_num(rithmic_trial.get("row_count"))))
        c4.metric("Trades", int(_num(event_counts.get("trade"))))
        c5.metric("Quotes", int(_num(event_counts.get("quote"))))
        st.caption("This is observed Paper/Chicago market-data evidence. It is not a promoted model and it is not order latency.")
        if rithmic_trial.get("report_binding_status") and rithmic_trial.get("report_binding_status") != "PASS":
            st.error("Report binding is blocked. These reports are not trusted until they match the raw capture checksum, normalized file, and replay artifact.")
            _display_df(
                rithmic_trial.get("report_binding_issues") or [],
                {
                    "artifact": "Artifact",
                    "issue": "Issue",
                    "expected": "Expected",
                    "actual": "Actual",
                },
            )
        if event_counts:
            event_frame = pd.DataFrame(
                [{"event_type": key, "count": value} for key, value in event_counts.items()]
            )
            event_frame["count"] = pd.to_numeric(event_frame["count"], errors="coerce")
            chart = (
                alt.Chart(event_frame)
                .mark_bar()
                .encode(
                    x=alt.X("event_type:N", title="Event type"),
                    y=alt.Y("count:Q", title="Count"),
                    tooltip=[
                        alt.Tooltip("event_type:N", title="Event"),
                        alt.Tooltip("count:Q", title="Rows", format=","),
                    ],
                )
            )
            _altair_chart(chart)
        checks = rithmic_trial.get("quality_checks") or {}
        if checks:
            _display_df(
                [{"check": key, "value": value} for key, value in checks.items()],
                {"check": "Quality check", "value": "Value"},
            )
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
    lane_registry = registry.get("lane_registry") or {}
    if lane_registry.get("status") == "BLOCKING":
        st.subheader("Lane Registry Blocker")
        st.error("Lane registry did not load cleanly. Candidate discovery and monitoring are blocked until this is fixed.")
        if lane_registry.get("errors"):
            _display_df(
                lane_registry["errors"],
                {
                    "lane": "Lane",
                    "stage": "Stage",
                    "status": "Status",
                    "error": "Error",
                },
            )
        if lane_registry.get("blocking_gates"):
            _display_df(
                lane_registry["blocking_gates"],
                {
                    "gate": "Gate",
                    "status": "Status",
                    "lane": "Lane",
                    "reason": "Reason",
                },
            )
    lanes = registry.get("lanes") or []
    if lanes:
        st.subheader("Registered model lanes")
        _display_df(
            lanes,
            {
                "lane": "Lane",
                "source": "Workbench source",
                "symbols": "Symbols",
                "event_types": "Event types",
                "capability": "Capability",
                "is_hft": "HFT",
                "dma": "DMA",
                "node_direct": "Node direct",
                "load_status": "Config",
            },
        )
    _json_expander("Symbol / universe", data.get("universe") or data.get("periods"))
    _json_expander("Bitcoin node / mempool evidence", data.get("btc_node"))
    _json_expander("Bitcoin state packet transport", edge_packets)


def render_backtest_evidence(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Smoke Diagnostics & Backtest Gates")
    render_run_header(snapshot)
    rows = snapshot.backtest.get("rows") or []
    hft_rows = snapshot.backtest.get("hft_validation_rows") or []
    leaderboard = snapshot.backtest.get("proxy_leaderboard") or []
    equity_curves = snapshot.backtest.get("equity_curves") or {}
    holdout_rows = snapshot.backtest.get("holdout_stage_rows") or []
    control_rows = snapshot.backtest.get("negative_control_rows") or []
    vectorbt_summary = snapshot.backtest.get("vectorbt_summary") or {}
    coverage_rows = (snapshot.system or {}).get("pipeline_coverage") or []
    coverage_by_stage = {str(row.get("stage")): row for row in coverage_rows if isinstance(row, dict)}
    rithmic_trial = snapshot.backtest.get("rithmic_trial") or {}

    if rithmic_trial:
        summary = snapshot.backtest.get("summary") or {}
        event_counts = summary.get("event_type_counts") or {}
        st.subheader("CME Rithmic Replay")
        c1, c2, c3, c4, c5 = st.columns(5)
        binding_status = str(summary.get("report_binding_status") or "missing").upper()
        c1.metric("Binding", binding_status)
        c2.metric("Replay", str(summary.get("hftbacktest_conversion_status") or "missing").upper())
        c3.metric("Mode", str(summary.get("mode") or "unknown"))
        c4.metric("Events", int(_num(summary.get("event_count"))))
        c5.metric("Order ack pairs", int(_num((snapshot.latency.get("rithmic_order_ack") or {}).get("paired_count"))))
        if binding_status != "PASS":
            st.error("Replay evidence is blocked because the reports do not bind to the selected raw capture.")
            _display_df(
                summary.get("report_binding_issues") or [],
                {
                    "artifact": "Artifact",
                    "issue": "Issue",
                    "expected": "Expected",
                    "actual": "Actual",
                },
            )
        st.caption(f"Data quality: {str(summary.get('data_quality_status') or 'missing').upper()}")
        st.caption(
            "This tab is showing the data/replay lane. P&L appears after a model run emits strategy trades; "
            "this capture only proves Paper/Chicago data and trade-only replay are working."
        )
        if event_counts:
            event_frame = pd.DataFrame(
                [{"event_type": key, "count": value} for key, value in event_counts.items()]
            )
            event_frame["count"] = pd.to_numeric(event_frame["count"], errors="coerce")
            chart = (
                alt.Chart(event_frame)
                .mark_bar()
                .encode(
                    x=alt.X("event_type:N", title="Event type"),
                    y=alt.Y("count:Q", title="Rows"),
                    color=alt.Color("event_type:N", legend=None),
                    tooltip=[
                        alt.Tooltip("event_type:N", title="Event"),
                        alt.Tooltip("count:Q", title="Rows", format=","),
                    ],
                )
            )
            _altair_chart(chart)
        limitations = summary.get("limitations") or []
        if limitations:
            _display_df(
                [{"limitation": item} for item in limitations],
                {"limitation": "Replay limitation"},
            )
        if rows:
            _display_df(
                rows,
                {
                    "candidate_id": "Run",
                    "target": "Target",
                    "pass_fail": "Gate",
                    "rows": "Rows",
                    "proxy_trades": "Trades",
                    "quotes": "Quotes",
                    "depth_events": "Depth",
                    "mode": "Replay mode",
                    "npz_path": "Replay NPZ",
                },
            )

    if leaderboard:
        lb = _leaderboard_frame(leaderboard)
        c1, c2, c3, c4 = st.columns(4)
        top = leaderboard[0]
        c1.metric(
            "Top diagnostic P&L",
            _fmt_bps(top.get("proxy_net_pnl_bps"), decimals=0),
            help="Smoke-layer P&L built from purged walk-forward out-of-sample rows before VectorBT and execution replay.",
        )
        c2.metric("Diagnostic leader", _strategy_label(top.get("candidate_id")))
        c3.metric("Smoke OOS trades", int(_num(top.get("proxy_trades"))))
        c4.metric("Worst drawdown", _fmt_bps(top.get("proxy_max_drawdown_bps")))
        st.caption("Diagnostic only: proxy P&L is not venue fills and is not a promotion gate.")
        if not lb.empty:
            st.subheader("Diagnostic P&L ranking")
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
            st.subheader("Smoke OOS equity proxy")
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
            st.subheader("Recent smoke OOS proxy tape")
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

    if not rithmic_trial:
        st.subheader("Smoke OOS diagnostic rows")
        st.caption("These rows come from smoke-layer purged OOS diagnostics. Full execution replay appears below only after VectorBT promotes a registry candidate.")
        _display_df(
            rows,
            {
                "candidate_id": "Candidate",
                "hypothesis_id": "Hypothesis",
                "target": "Target",
                "pass_fail": "Smoke gate",
                "oos_ic": "OOS IC",
                "rows": "OOS rows",
                "folds": "Folds",
                "holdout": "Holdout",
                "proxy_net_pnl_bps": "Proxy net bps",
                "proxy_trades": "Proxy trades",
                "proxy_max_drawdown_bps": "Proxy drawdown bps",
                "proxy_sharpe": "Sharpe proxy",
            },
        )
    if snapshot.source == "crypto_lane":
        st.subheader("VectorBT Filter")
        if vectorbt_summary:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Status", str(vectorbt_summary.get("status") or "UNKNOWN"))
            c2.metric("Candidates checked", int(_num(vectorbt_summary.get("candidate_count"))))
            c3.metric("Adapter invoked", "yes" if vectorbt_summary.get("adapter_invoked") else "no")
            c4.metric("Promoted", len(vectorbt_summary.get("promoted_candidate_ids") or []))
            c5.metric("Rejected", len(vectorbt_summary.get("rejected_candidate_ids") or []))
            st.caption(f"Package available: {'yes' if vectorbt_summary.get('vectorbt_available') else 'no'}")
            backends = vectorbt_summary.get("filter_backends") or []
            if backends:
                st.caption(f"Filter backend: {', '.join(str(b) for b in backends)}")
            rejection_reasons = vectorbt_summary.get("rejection_reasons") or {}
            if rejection_reasons:
                _display_df(
                    [
                        {"reason": reason, "count": count}
                        for reason, count in sorted(rejection_reasons.items(), key=lambda item: str(item[0]))
                    ],
                    {"reason": "Rejection reason", "count": "Count"},
                )
            signal_source = vectorbt_summary.get("signal_source_contract") or {}
            if signal_source:
                st.subheader("Safe OOS Signal Source")
                st.caption(
                    "VectorBT signals are built from run-local purged OOS prediction positions aligned by exchange timestamp. "
                    "Forward labels and proxy P&L are not used to create entries or exits."
                )
                _display_df(
                    [
                        {"field": "Adapter", "value": signal_source.get("adapter", "")},
                        {"field": "Input artifact", "value": signal_source.get("input_artifact", "")},
                        {"field": "Timestamp join", "value": signal_source.get("alignment", "")},
                        {"field": "Labels used for signal", "value": signal_source.get("labels_used_for_signal", False)},
                        {"field": "Proxy P&L used for signal", "value": signal_source.get("proxy_pnl_used_for_signal", False)},
                    ],
                    {"field": "Check", "value": "Value"},
                )
            signal_contract = vectorbt_summary.get("signal_adapter_rejection_contract") or {}
            if signal_contract:
                st.subheader("Signal Adapter Rejection")
                st.caption(str(signal_contract.get("why_blocking") or "A crypto signal adapter rejection blocked VectorBT promotion."))
                _display_df(
                    [
                        {"required": item}
                        for item in (signal_contract.get("required_for_adapter_acceptance") or [])
                    ],
                    {"required": "Required for adapter acceptance"},
                )
                _json_expander("Existing repo sources for this boundary", signal_contract.get("existing_repo_sources"))
            if not vectorbt_summary.get("observed"):
                st.warning(str(vectorbt_summary.get("reason") or "VectorBT filter evidence is not observed."))
            _json_expander("VectorBT summary", vectorbt_summary)
        else:
            st.warning("No vectorBT filter artifact is attached to this selected crypto run.")
    if hft_rows:
        st.subheader("Crypto Execution Replay")
        st.caption(
            "Validation rows from replay reports. Only L3_VALIDATED or FULL_EXECUTION rows satisfy the execution replay gate; L2 proxy rows are diagnostic."
        )
        _display_df(
            hft_rows,
            {
                "candidate_id": "Candidate",
                "classification": "Execution class",
                "validation_path": "Validation path",
                "num_trades": "Trades",
                "num_intents": "Intents",
                "fill_rate": "Fill rate",
                "net_pnl": "Net P&L",
                "slippage_bps": "Slippage bps",
                "adverse_selection_cost": "Adverse selection",
                "tail_loss": "Tail loss",
                "sharpe_ratio": "Sharpe",
                "error": "Error",
                "npz_path": "NPZ",
            },
        )
    elif snapshot.source == "crypto_lane":
        hft_coverage = coverage_by_stage.get("hftbacktest_replay") or {}
        st.subheader("Crypto Execution Replay")
        st.warning(str(hft_coverage.get("reason") or "No crypto execution replay artifact is attached to this selected crypto run."))
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
    render_run_header(snapshot)
    latency = snapshot.latency or {}
    rithmic_endpoint = latency.get("rithmic_endpoint") or {}
    if rithmic_endpoint:
        st.subheader("Rithmic Endpoint")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Profile", rithmic_endpoint.get("profile", "unknown"))
        c2.metric("System", rithmic_endpoint.get("system", ""))
        c3.metric("Gateway", rithmic_endpoint.get("gateway", ""))
        c4.metric("Status", rithmic_endpoint.get("status", "UNKNOWN"))
        missing = rithmic_endpoint.get("missing_endpoint_params") or []
        if rithmic_endpoint.get("reason_code") == "PAPER_ENDPOINT_PARAMS_MISSING":
            st.error("Paper/Chicago API parameters are missing; credentials alone are not an endpoint.")
        elif rithmic_endpoint.get("reason_code") == "RITHMIC_CREDENTIALS_MISSING":
            if (latency.get("rithmic_capture_endpoint") or {}).get("status"):
                st.warning("Current Workbench server credentials are not loaded. The selected capture has separate last-connection evidence below.")
            else:
                st.error("Paper/Chicago endpoint parameters are present, but runtime credentials are not loaded.")
        elif rithmic_endpoint.get("reason_code") == "GATEWAY_LIBRARY_NOT_FOUND":
            st.error("Paper/Chicago endpoint parameters are present, but the C++ Rithmic gateway library is not available.")
        elif rithmic_endpoint.get("reason_code"):
            st.warning(str(rithmic_endpoint.get("reason_code")))
        if missing:
            _df([{"missing_parameter": item} for item in missing])
        rithmic_order_ack = latency.get("rithmic_order_ack") or {}
        if rithmic_order_ack:
            _display_df(
                [rithmic_order_ack],
                {
                    "scope": "Scope",
                    "status": "Ack status",
                    "reason_code": "Reason",
                    "order_ack_measured": "Measured",
                    "endpoint_profile": "Endpoint",
                    "paired_count": "Pairs",
                },
            )
        capture_endpoint = latency.get("rithmic_capture_endpoint") or {}
        if capture_endpoint:
            st.subheader("Last Capture Endpoint Evidence")
            _display_df(
                [capture_endpoint],
                {
                    "profile": "Profile",
                    "system": "System",
                    "gateway": "Gateway",
                    "status": "Capture status",
                    "reason_code": "Reason",
                    "secret_exposed": "Secret exposed",
                },
            )
    ibkr_endpoint = latency.get("ibkr_endpoint") or {}
    if ibkr_endpoint:
        st.subheader("IBKR Equities Endpoint")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Profile", ibkr_endpoint.get("profile", "unknown"))
        c2.metric("Transport", ibkr_endpoint.get("transport", ""))
        c3.metric("Socket", f"{ibkr_endpoint.get('host', '')}:{ibkr_endpoint.get('port', '')}")
        c4.metric("Status", ibkr_endpoint.get("status", "UNKNOWN"))
        api = ibkr_endpoint.get("api") or {}
        socket_state = ibkr_endpoint.get("socket") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Socket reachable", bool(socket_state.get("reachable")))
        c2.metric("API package", bool(api.get("api_package_present")))
        c3.metric("Headless handshake", api.get("api_client_status", "not_checked"))
        c4.metric("Account env set", bool((ibkr_endpoint.get("credentials") or {}).get("account_id_set")))
        c1, c2 = st.columns(2)
        c1.metric("Pipeline gate", ibkr_endpoint.get("pipeline_gate_status", "UNKNOWN"))
        c2.metric("Live routing gate", ibkr_endpoint.get("live_routing_gate_status", "UNKNOWN"))
        if ibkr_endpoint.get("pipeline_note"):
            st.info(str(ibkr_endpoint.get("pipeline_note")))
        if ibkr_endpoint.get("headless_handshake_required"):
            st.caption("Headless API handshake is required for this Workbench lane gate.")
        if api.get("api_client_status") == "PAPER_DISCLAIMER_PENDING":
            st.warning(
                "IBKR rejected the headless API handshake with paper-disclaimer error 10141. "
                "Live routing remains gated, but this no longer blocks the equities/options research pipeline."
            )
            errors = api.get("errors") or []
            if errors:
                _display_df(
                    errors,
                    {
                        "code": "Code",
                        "message": "Message",
                    },
                )
        if ibkr_endpoint.get("blocking_gates"):
            if ibkr_endpoint.get("pipeline_blocking"):
                st.error(
                    "IBKR equities endpoint has an infrastructure blocker for this lane."
                )
            else:
                st.warning(
                    "IBKR equities live routing is gated. Model production, backtest, robustness, and registry work can continue."
                )
            _display_df(
                ibkr_endpoint["blocking_gates"],
                {
                    "gate": "Gate",
                    "status": "Status",
                    "reason": "Reason",
                },
            )
        st.caption(
            "QuantX reference: TWS/IB Gateway socket path, paper port 7497, live port 7496. "
            "Client Portal is not the default equities lane endpoint."
        )
    latency_baseline = latency.get("latency_baseline") or {}
    if latency_baseline:
        metrics = latency_baseline.get("metrics") or {}
        artifacts = latency_baseline.get("broker_artifacts") or {}
        baseline_role = str(latency_baseline.get("_baseline_role") or latency_baseline.get("baseline_role") or "latest_observed_summary")
        role_label = "CURRENT BASE" if baseline_role == "current_baseline" else "LATEST RUN"
        records_written = int(_num(artifacts.get("records_written")))
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Latency base", role_label)
        c2.metric("Tick→trigger p50", _latency_metric_label(metrics, "tick_to_send_trigger_us"))
        c3.metric("SDK return p50", _latency_metric_label(metrics, "tick_to_send_us"))
        c4.metric("Send→ack p50", _latency_metric_label(metrics, "send_to_ack_us"))
        c5.metric("Hot path", str(artifacts.get("hot_path_language") or "unknown").upper())
        st.caption(f"Run: `{latency_baseline.get('run_id') or ''}`")
        st.caption(f"Stop reason: `{artifacts.get('stop_reason') or 'completed'}`")
        if baseline_role == "current_baseline":
            st.success(str(latency_baseline.get("baseline_reason") or "This is the accepted current latency baseline."))
        if records_written <= 0:
            st.error(
                "Latest native latency run produced no paired samples. "
                f"Blocker: {artifacts.get('stop_reason') or 'missing_samples'}."
            )
        st.caption(
            "Latest broker baseline. Tick→trigger is the internal C++ placement trigger at Rithmic SDK call entry; "
                "SDK return is the synchronous sendOrder call returning; send→ack is Rithmic/broker response latency. "
                "Promotion-authoritative runs must show a native C++ hot path with no Python broker wrapper."
        )
        runtime_tuning = latency_baseline.get("runtime_tuning") or {}
        operating_profile = latency_baseline.get("operating_profile") or {}
        if runtime_tuning or operating_profile:
            st.subheader("Accepted Speed Profile")
            profile_rows = [
                {"setting": "Host", "value": operating_profile.get("host", "")},
                {"setting": "Gateway", "value": operating_profile.get("gateway", "")},
                {"setting": "Symbol", "value": f"{operating_profile.get('symbol', '')} {operating_profile.get('exchange', '')}".strip()},
                {"setting": "CPU affinity", "value": f"requested={runtime_tuning.get('requested_cpu', operating_profile.get('rithmic_probe_cpu', ''))}, applied={runtime_tuning.get('affinity_applied', '')}"},
                {"setting": "Realtime priority", "value": f"requested={runtime_tuning.get('requested_rt_priority', operating_profile.get('rithmic_probe_rt_priority', ''))}, applied={runtime_tuning.get('rt_priority_applied', '')}"},
                {"setting": "Memory lock", "value": f"requested={runtime_tuning.get('memory_lock_requested', '')}, applied={runtime_tuning.get('memory_lock_applied', '')}"},
                {"setting": "Prefault bytes", "value": runtime_tuning.get("prefault_bytes", operating_profile.get("rithmic_probe_prefault_bytes", ""))},
                {"setting": "Market-data dependency", "value": f"skip_md={operating_profile.get('rithmic_probe_skip_md', '')}, require_md={operating_profile.get('rithmic_probe_require_md', '')}"},
            ]
            _display_df(
                profile_rows,
                {
                    "setting": "Setting",
                    "value": "Accepted value",
                },
            )
        rows = [
            _latency_metric_row(metrics, "tick_to_decision_us", "Tick→decision", "placement"),
            _latency_metric_row(metrics, "decision_to_send_trigger_us", "Decision→trigger", "placement trigger"),
            _latency_metric_row(metrics, "tick_to_send_trigger_us", "Tick→trigger", "placement trigger"),
            _latency_metric_row(metrics, "decision_to_send_us", "Decision→SDK return", "native API"),
            _latency_metric_row(metrics, "tick_to_send_us", "Tick→SDK return", "native API"),
            _latency_metric_row(metrics, "rithmic_send_call_us", "Rithmic send call", "native API"),
            _latency_metric_row(metrics, "send_to_ack_us", "Send→ack", "broker response"),
            _latency_metric_row(metrics, "cancel_to_send_us", "Cancel→send", "defensive"),
            _latency_metric_row(metrics, "cancel_to_ack_us", "Cancel→ack", "broker response"),
        ]
        _display_df(
            rows,
            {
                "view": "View",
                "metric": "Metric",
                "count": "Samples",
                "p50_us": "p50 us",
                "p90_us": "p90 us",
                "p95_us": "p95 us",
                "p99_us": "p99 us",
                "p99_9_us": "p99.9 us",
                "max_us": "max us",
            },
        )
        _display_df(
            [
                {
                    "artifact": "summary",
                    "path": latency_baseline.get("_path", ""),
                },
                {
                    "artifact": "samples",
                    "path": latency_baseline.get("_sample_path") or latency_baseline.get("sample_path", ""),
                },
                {
                    "artifact": "raw_events",
                    "path": artifacts.get("raw_events_path", ""),
                },
                {
                    "artifact": "hot_path",
                    "path": f"language={artifacts.get('hot_path_language') or 'unknown'}, wrapper={artifacts.get('wrapper') or 'unknown'}",
                },
            ],
            {"artifact": "Artifact", "path": "Path"},
        )
    feed_latency = latency.get("feed_latency_us") or {}
    if feed_latency:
        st.subheader("Rithmic Feed Timing")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Feed rows", int(_num(feed_latency.get("count"))))
        c2.metric("p50 us", f"{_num(feed_latency.get('p50_us')):,.1f}")
        c3.metric("p90 us", f"{_num(feed_latency.get('p90_us')):,.1f}")
        c4.metric("p99 us", f"{_num(feed_latency.get('p99_us')):,.1f}")
        c5.metric("Max us", f"{_num(feed_latency.get('max_us')):,.1f}")
        st.caption(
            "Feed timing is server/local timestamp evidence. Submit-to-ack latency is separate and requires paired tagged order events."
        )
        if any(_num(feed_latency.get(field)) < 0 for field in ("p50_us", "p90_us", "p99_us")):
            st.warning(
                "Feed timing has negative clock-derived values. Treat this as timestamp alignment evidence, not usable latency."
            )
    rows = latency.get("execution_ack_rows") or []
    if rows:
        measured = sum(1 for row in rows if row.get("measured"))
        blocked = sum(1 for row in rows if str(row.get("status", "")).upper().startswith("INSUFFICIENT"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Submit→ack measured", measured)
        c2.metric("Insufficient ack rows", blocked)
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
    elif latency.get("latency_operating_envelope") or latency.get("campaign_latency_operating_envelope"):
        envelope = latency.get("latency_operating_envelope") or latency.get("campaign_latency_operating_envelope")
        offensive = envelope.get("offensive") or {}
        placement = offensive.get("placement") or {}
        tick_to_trigger = placement.get("tick_to_send_trigger_us") or {}
        tick_to_send = placement.get("tick_to_send_us") or {}
        external = envelope.get("external_confirmation") or {}
        confirmation = external.get("confirmation") or {}
        send_to_ack = confirmation.get("send_to_ack_us") or {}
        pending = envelope.get("pending_state_risk") or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Envelope", str(envelope.get("status", "unknown")))
        c2.metric("Tick→trigger p99", _envelope_metric_label(tick_to_trigger, "p99"))
        c3.metric("Send→ack p99", _envelope_metric_label(send_to_ack, "p99"))
        c4.metric("Placement band", str(offensive.get("operating_band", "unknown")))
        st.caption("Trigger speed, SDK return, and acknowledgment latency are separate C++ evidence boundaries; acknowledgments update local state asynchronously.")
        _display_df(
            [
                {"boundary": "Tick to trigger", "p50_us": tick_to_trigger.get("p50"), "p99_us": tick_to_trigger.get("p99")},
                {"boundary": "Tick to SDK return", "p50_us": tick_to_send.get("p50"), "p99_us": tick_to_send.get("p99")},
                {"boundary": "Send to ack", "p50_us": send_to_ack.get("p50"), "p99_us": send_to_ack.get("p99")},
            ],
            {"boundary": "Boundary", "p50_us": "p50 us", "p99_us": "p99 us"},
        )
        blockers = envelope.get("promotion_blockers") or []
        if blockers:
            _display_df(
                blockers,
                {
                    "gate": "Gate",
                    "status": "Status",
                    "reason": "Reason",
                },
            )
        _json_expander("Pending state risk", pending)
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
    elif latency_baseline:
        st.caption("Broker latency baseline is shown above.")
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
    render_run_header(snapshot)
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
    feature_fabric = diagnostics.get("feature_fabric") or {}
    if feature_fabric:
        st.subheader("Cross-Lane Feature Fabric")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fabric status", feature_fabric.get("status", "UNKNOWN"))
        c2.metric("PIT gate", feature_fabric.get("pit_validation_status", "UNKNOWN"))
        c3.metric("Consumer lane", feature_fabric.get("consumer_lane", ""))
        c4.metric("Allowed source lanes", len(feature_fabric.get("allowed_source_lanes") or []))
        c1, c2 = st.columns(2)
        c1.metric("Catalog PIT eligibility", feature_fabric.get("catalog_pit_eligibility_status", "UNKNOWN"))
        c2.metric("Model feature usage", feature_fabric.get("model_feature_usage_status", "not_observed"))
        st.caption(
            "Catalog rows prove configured feature eligibility and PIT safety. "
            "They do not claim a live model consumed those features."
        )
        if feature_fabric.get("gate_status") == "BLOCKING":
            st.error("Cross-lane feature fabric is blocked. The Workbench will not treat this policy as evidence until artifacts and PIT validation pass.")
            if feature_fabric.get("blocking_gates"):
                _display_df(
                    feature_fabric["blocking_gates"],
                    {
                        "gate": "Gate",
                        "status": "Status",
                        "reason": "Reason",
                        "issue_count": "Issues",
                        "missing_artifacts": "Missing artifacts",
                    },
                )
            if feature_fabric.get("pit_issues"):
                st.subheader("PIT validation issues")
                _display_df(
                    feature_fabric["pit_issues"],
                    {
                        "feature": "Feature",
                        "issue": "Issue",
                        "value": "Value",
                        "available_timestamp": "Available time",
                        "decision_timestamp": "Decision time",
                        "pit_status": "PIT status",
                    },
                )
        if feature_fabric.get("rows"):
            _display_df(
                feature_fabric["rows"],
                {
                    "feature": "Feature",
                    "feature_name": "Feature",
                    "source_lane": "Source lane",
                    "asset": "Asset",
                    "source_timestamp": "Source time",
                    "available_timestamp": "Available time",
                    "source_available_timestamp": "Available time",
                    "decision_timestamp": "Decision time",
                    "pit_status": "PIT status",
                    "evidence_scope": "Evidence scope",
                    "consumer_model": "Consumer model",
                },
            )
        else:
            _df(
                [
                    {
                        "policy": feature_fabric.get("policy"),
                        "pit_rule": feature_fabric.get("pit_rule"),
                        "artifact_root": feature_fabric.get("artifact_root"),
                    }
                ]
            )
        links = feature_fabric.get("artifact_paths") or feature_fabric.get("expected_artifacts") or {}
        if links:
            with st.expander("Feature fabric artifacts"):
                _df([{"artifact": k, "path": v} for k, v in links.items()])
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
    render_run_header(snapshot)
    robustness = snapshot.robustness or {}
    summary = robustness.get("crypto_robustness_summary") or {}
    explanation = robustness.get("explanation") or {}
    if summary:
        blocking_gates = explanation.get("blocking_gates") or summary.get("blocking_gates") or []
        failed_evidence = str(summary.get("status") or "").upper() == "FAIL" and bool(summary.get("observed"))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Overall result", explanation.get("aggregate_status") or str(summary.get("status") or "UNKNOWN"))
        c2.metric("Replay trades", int(_num(summary.get("trade_sample_count"))))
        c3.metric("Required failed", int(_num(explanation.get("required_fail_count"))))
        c4.metric("Required pending", int(_num(explanation.get("required_pending_count"))))
        if explanation.get("operator_explanation"):
            if failed_evidence:
                st.error(str(explanation["operator_explanation"]))
            elif explanation.get("required_pending_count"):
                st.warning(str(explanation["operator_explanation"]))
            else:
                st.info(str(explanation["operator_explanation"]))
        if explanation.get("failed_required_checks"):
            st.subheader("Failed Required Checks")
            failed_rows = [
                {"check": name, "why_it_blocks": "Required replay robustness gate did not pass."}
                for name in explanation["failed_required_checks"]
            ]
            _df(failed_rows)
        if blocking_gates:
            st.subheader("Aggregate Gate Reasons")
            _display_df(
                blocking_gates,
                {
                    "gate": "Gate",
                    "status": "Status",
                    "reason": "Reason",
                    "pending": "Pending checks",
                    "failed": "Failed checks",
                },
            )
        pack = summary.get("robustness_pack") or {}
        checks = pack.get("checks") or []
        if checks:
            st.subheader("Robustness pack checks")
            _display_df(
                checks,
                {
                    "name": "Check",
                    "status": "Status",
                    "passed": "Passed",
                    "observed_value": "Observed",
                    "threshold": "Threshold",
                    "comparison_operator": "Rule",
                    "reason_code": "Reason code",
                },
            )
        if robustness.get("artifact_links"):
            st.subheader("Robustness artifacts")
            _df([{"artifact": k, "path": v} for k, v in robustness["artifact_links"].items() if v])
        _json_expander("Crypto robustness summary", summary)
    if robustness.get("rows"):
        rows = robustness["rows"]
        st.subheader("Smoke robustness prerequisites")
        if summary and summary.get("observed"):
            st.caption("Smoke prerequisite checks are shown beside the replay robustness result; promotion depends on observed replay robustness, not proxy P&L.")
        else:
            st.caption("These are smoke-layer prerequisite checks. Replay robustness remains blocked until VectorBT promotes candidates and execution replay emits trade samples.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Smoke candidates", len(rows))
        c2.metric("Smoke holdout passes", sum(1 for r in rows if str(r.get("holdout", "")).lower() == "pass"))
        c3.metric("Smoke purged CV", sum(1 for r in rows if r.get("purged_cv")))
        _display_df(
            rows,
            {
                "candidate_id": "Candidate",
                "purged_cv": "Smoke purged CV",
                "purged_splits": "Splits",
                "holdout": "Smoke holdout",
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
    render_run_header(snapshot)
    decision = snapshot.decision or {}
    action = str(decision.get("action") or decision.get("decision") or "UNKNOWN")
    reason = str(decision.get("reason") or "")
    if action.upper() == "PROMOTE":
        st.success(f"{action}: {reason}")
    elif action.upper() == "REJECT":
        st.error(f"{action}: {reason}")
    elif action.upper() == "QUARANTINE":
        st.warning(f"{action}: {reason}")
    else:
        st.info(f"{action}: {reason}")
    c1, c2, c3, c4 = st.columns(4)
    evidence_candidate = decision.get("evidence_candidate_id") or decision.get("top_smoke_candidate")
    c1.metric("Evidence candidate", _short_id(evidence_candidate, keep=24))
    c2.metric("Smoke passes", int(_num(decision.get("smoke_pass_count"))))
    c3.metric("Positive P&L diagnostics", int(_num(decision.get("economic_diagnostic_pass_count"))))
    c4.metric("Live-ready registry", bool(decision.get("live_registry_ready")))
    institutional_metrics = decision.get("institutional_metrics") or (snapshot.system or {}).get("institutional_metrics") or {}
    scorecard = institutional_metrics.get("scorecard") or {}
    envelope = institutional_metrics.get("envelope") or {}
    if institutional_metrics:
        st.subheader("Institutional Model Scorecard")
        if institutional_metrics.get("status") == "observed" and scorecard:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Grade", scorecard.get("grade", "UNKNOWN"))
            m2.metric("Weighted score", f"{_num(scorecard.get('weighted_score')):.3f}")
            m3.metric("Metric count", len(scorecard.get("metrics") or []))
            m4.metric("Envelope active", bool(envelope.get("active")))
            categories = scorecard.get("category_scores") or []
            if categories:
                _df(
                    [
                        {
                            "category": row.get("category"),
                            "weight": row.get("weight"),
                            "score": row.get("normalized_score"),
                            "status": row.get("status"),
                            "explanation": row.get("explanation"),
                        }
                        for row in categories
                    ]
                )
            if envelope:
                st.caption(
                    "Behavior envelope monitors live/paper/shadow behavior against expected P&L, drawdown, slippage, fill, latency, regime, and feature bounds."
                )
                _json_expander("Behavior envelope", envelope)
        else:
            st.warning("Institutional model metrics are missing or failed for this selected run.")
            _json_expander("Metric calculation status", institutional_metrics)
    smoke_triage_order = decision.get("smoke_triage_order") or []
    if smoke_triage_order:
        st.subheader("Smoke triage order")
        st.caption("Pre-filter ordering only. VectorBT promotion, crypto execution replay, robustness, and venue execution gates remain separate.")
        _df(smoke_triage_order)
    vectorbt_promoted_order = decision.get("vectorbt_promoted_order") or []
    if vectorbt_promoted_order:
        st.subheader("VectorBT-promoted replay order")
        st.caption("Decision candidates must remain inside this promoted source set through replay, robustness, and execution-ack gates.")
        _df(vectorbt_promoted_order)
    _json_expander("Scoring summary", decision.get("scoring_summary"))
    blockers = decision.get("blocking_gates") or []
    if blockers:
        st.subheader("Evidence failures" if action.upper() == "REJECT" else "Blocking gates")
        _df(blockers)
    if decision.get("bitcoin_edge_packet_status"):
        st.caption(f"Bitcoin state packet gate: {decision['bitcoin_edge_packet_status']}")


def _live_stage_rows(snapshot: RunEvidenceSnapshot) -> list[dict[str, Any]]:
    tm = snapshot.trade_manager or {}
    decision = snapshot.decision or {}
    backtest = snapshot.backtest or {}
    robustness = snapshot.robustness or {}
    has_backtest = bool(backtest.get("rows") or backtest.get("reports") or backtest.get("campaigns") or backtest.get("observed"))
    robustness_failed = bool(robustness.get("failed") or (robustness.get("explanation") or {}).get("failed_required_checks"))
    robustness_observed = bool(robustness.get("rows") or robustness.get("gates") or robustness.get("crypto_robustness_summary"))
    live_ready = bool(decision.get("live_registry_ready"))
    promoted_count = int(_num(tm.get("promoted_model_count")))
    active_models = tm.get("active_models") or []
    order_intents = tm.get("order_intents") or []
    order_states = tm.get("latest_order_states") or []
    fills = tm.get("fills") or []
    positions = tm.get("open_positions") or []
    kill_switch = tm.get("kill_switch") or {}
    link_status = str(tm.get("selected_run_link_status") or "").upper()
    linked_to_selected = link_status in {"MATCHED", "NOT_SELECTED"}

    def session_status(has_rows: bool) -> str:
        if not has_rows:
            return "NOT_OBSERVED"
        return "OBSERVED" if linked_to_selected else "UNLINKED"

    return [
        {
            "phase": "1. Candidate discovery",
            "status": "OBSERVED" if snapshot.registry else "NOT_OBSERVED",
            "operator_state": snapshot.run_id or "no selected run",
        },
        {
            "phase": "2. Backtest evidence",
            "status": "OBSERVED" if has_backtest else "NOT_OBSERVED",
            "operator_state": f"{len(backtest.get('rows') or [])} rows",
        },
        {
            "phase": "3. Robustness / walk-forward",
            "status": "BLOCKED" if robustness_failed else ("OBSERVED" if robustness_observed else "NOT_OBSERVED"),
            "operator_state": (robustness.get("explanation") or {}).get("aggregate_status") or snapshot.state,
        },
        {
            "phase": "4. Decision gate",
            "status": str(decision.get("action") or decision.get("decision") or "UNKNOWN").upper(),
            "operator_state": decision.get("reason", ""),
        },
        {
            "phase": "5. Registry promotion",
            "status": "READY" if live_ready else ("PROMOTED_RECORDS" if promoted_count else "BLOCKED"),
            "operator_state": f"{promoted_count} promoted model records",
        },
        {
            "phase": "6. Trade Manager active model",
            "status": session_status(bool(active_models)),
            "operator_state": f"{len(active_models)} active models; {tm.get('selected_run_link_reason', '')}",
        },
        {
            "phase": "7. Signal / order intent",
            "status": session_status(bool(order_intents)),
            "operator_state": f"{len(order_intents)} order intents",
        },
        {
            "phase": "8. Risk and order state",
            "status": session_status(bool(order_states)),
            "operator_state": f"{len(order_states)} latest order states",
        },
        {
            "phase": "9. Fills / positions / P&L",
            "status": session_status(bool(fills or positions or tm.get("pnl_latest"))),
            "operator_state": f"{len(fills)} fills, {len(positions)} open positions",
        },
        {
            "phase": "10. Kill switch",
            "status": str(kill_switch.get("status") or "NOT_OBSERVED").upper(),
            "operator_state": "active" if kill_switch.get("active") else "clear/not observed",
        },
        {
            "phase": "11. Live routing",
            "status": str(tm.get("live_routing_status") or "NOT_WIRED"),
            "operator_state": tm.get("live_routing_reason", ""),
        },
    ]


def render_live_monitor(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Live Monitor")
    render_run_header(snapshot)
    tm = snapshot.trade_manager or {}
    status = str(tm.get("status") or "not_observed")
    session_id = tm.get("session_id") or "none"
    kill_switch = tm.get("kill_switch") or {}
    pnl_latest = tm.get("pnl_latest") or {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Session", session_id)
    c2.metric("Active models", len(tm.get("active_models") or []))
    c3.metric("Open positions", len(tm.get("open_positions") or []))
    c4.metric("Open orders", len(tm.get("open_orders") or []))
    c5.metric("Kill switch", str(kill_switch.get("status") or "NOT_OBSERVED"))
    c6.metric("Run link", str(tm.get("selected_run_link_status") or "NO_SESSION"))
    if status == "artifact_error":
        st.error(tm.get("reason") or "Trade Manager session artifacts could not be loaded safely.")
    elif status != "observed":
        st.warning(tm.get("reason") or "No Trade Manager session artifacts observed.")
    elif kill_switch.get("active"):
        st.error("Kill switch active.")
    else:
        st.success("Trade Manager session artifacts observed.")
    if tm.get("selected_run_link_reason"):
        st.caption(str(tm["selected_run_link_reason"]))

    st.subheader("Pipeline To Live Trading")
    _df(_live_stage_rows(snapshot))

    st.subheader("Active Models")
    _display_df(
        tm.get("active_models") or [],
        {
            "model_id": "model",
            "status": "status",
            "activation_status": "activation",
            "symbols": "symbols",
            "allowed_symbols": "allowed symbols",
            "run_id": "run",
            "registry_id": "registry",
        },
    )

    st.subheader("Positions And Orders")
    p1, p2 = st.columns(2)
    with p1:
        st.caption("Open positions")
        _display_df(
            tm.get("open_positions") or [],
            {
                "timestamp_ns": "timestamp_ns",
                "symbol": "symbol",
                "quantity": "qty",
                "net_position": "net",
                "positions": "positions",
                "status": "status",
            },
        )
    with p2:
        st.caption("Open orders")
        _display_df(
            tm.get("open_orders") or [],
            {
                "order_intent_id": "intent",
                "order_id": "order",
                "model_id": "model",
                "symbol": "symbol",
                "state": "state",
                "timestamp_ns": "timestamp_ns",
                "risk_reason": "risk",
            },
        )

    st.subheader("P&L, Fills, And Rejections")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total P&L", _num(pnl_latest.get("total_pnl")))
    m2.metric("Realized", _num(pnl_latest.get("realized_pnl")))
    m3.metric("Unrealized", _num(pnl_latest.get("unrealized_pnl")))
    m4.metric("Drawdown", _num(pnl_latest.get("drawdown")))
    pnl_rows = tm.get("pnl_timeseries") or []
    if pnl_rows:
        frame = pd.DataFrame(pnl_rows)
        if "timestamp_ns" in frame.columns and any(col in frame.columns for col in ("total_pnl", "realized_pnl", "unrealized_pnl")):
            plot_cols = [col for col in ("total_pnl", "realized_pnl", "unrealized_pnl") if col in frame.columns]
            melted = frame[["timestamp_ns", *plot_cols]].melt("timestamp_ns", var_name="series", value_name="pnl")
            _altair_chart(alt.Chart(melted).mark_line(point=True).encode(x="timestamp_ns:O", y="pnl:Q", color="series:N"))
    f1, f2 = st.columns(2)
    with f1:
        st.caption("Recent fills")
        _display_df(
            tm.get("fills") or [],
            {
                "timestamp_ns": "timestamp_ns",
                "order_id": "order",
                "symbol": "symbol",
                "quantity": "qty",
                "price": "price",
                "model_id": "model",
            },
        )
    with f2:
        st.caption("Risk rejections")
        _display_df(
            tm.get("risk_rejections") or [],
            {
                "timestamp_ns": "timestamp_ns",
                "order_intent_id": "intent",
                "model_id": "model",
                "symbol": "symbol",
                "reason": "reason",
                "risk_reason": "risk",
                "status": "status",
            },
        )

    st.subheader("Execution Health")
    h1, h2, h3 = st.columns(3)
    latency = tm.get("latency") or {}
    slippage = tm.get("slippage") or {}
    h1.metric("Latency p99 ns", _num(latency.get("p99_ns")))
    h2.metric("Latency status", str(latency.get("status") or "NOT_OBSERVED"))
    h3.metric("Slippage status", str(slippage.get("status") or "NOT_OBSERVED"))
    _display_df(
        tm.get("incidents") or [],
        {
            "timestamp_ns": "timestamp_ns",
            "severity": "severity",
            "incident_id": "incident",
            "message": "message",
            "reason": "reason",
        },
    )
    _json_expander("Trade Manager Session Snapshot", tm)


def render_reports_analyst(snapshot: RunEvidenceSnapshot) -> None:
    st.header("Reports & Analyst")
    render_run_header(snapshot)
    reports = snapshot.reports or {}
    after_action = snapshot.after_action or {}
    relationships = snapshot.relationships or {}
    if after_action:
        st.subheader("After-Action Packet")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("GPT-5.5 gate", after_action.get("gate_status", "UNKNOWN"))
        c2.metric("Symbolic passed", after_action.get("symbolic_passed"))
        c3.metric("Report written", bool(after_action.get("report_written")))
        c4.metric("Response written", bool(after_action.get("response_written")))
        if not after_action.get("passed"):
            st.error(after_action.get("blocking_reason") or "GPT-5.5 xhigh after-action gate failed.")
        elif after_action.get("skip_reasons"):
            st.warning("GPT-5.5 xhigh runtime reason: " + ", ".join(map(str, after_action["skip_reasons"])))
        response = after_action.get("response") or {}
        providers = (snapshot.system or {}).get("llm_providers") or {}
        llm_provider = providers.get("openai_compatible_llm") or {}
        llm_target = (
            response.get("llm_model")
            or after_action.get("llm_model")
            or llm_provider.get("model_target")
            or "gpt-5.5 xhigh"
        )
        if response.get("llm_status"):
            st.caption(
                f"GPT-5.5 xhigh lane: `{response.get('llm_status')}` "
                f"target `{llm_target}`. "
                "This is advisory only."
            )
        if after_action.get("paths"):
            st.subheader("After-action artifacts")
            _df([{"artifact": k, "path": v} for k, v in after_action["paths"].items()])
        kg_slice = after_action.get("kg_slice") or {}
        if kg_slice:
            c1, c2 = st.columns(2)
            c1.metric("KG slice nodes", len(kg_slice.get("nodes") or []))
            c2.metric("KG slice edges", len(kg_slice.get("edges") or []))
        report_text = read_text(Path(str((after_action.get("paths") or {}).get("report", ""))))
        if report_text:
            st.markdown(report_text)
        else:
            narrative = str(response.get("narrative_md") or "")
            if narrative:
                st.markdown(narrative)
            else:
                st.info("No LLM narrative was written for this run; the after-action packet, symbolic result, KG slice, and skip reason are still available.")
    if relationships:
        st.subheader("AlphaGeometry/OpenFoundry Relationship Review")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Candidates", int(_num(relationships.get("candidate_count"))))
        c2.metric("Validated", int(_num(relationships.get("validated_count"))))
        c3.metric("Rejected", int(_num(relationships.get("rejected_count"))))
        c4.metric("Promotion authority", bool(relationships.get("promotion_authority")))
        st.caption("Relationship candidates are review artifacts only. No KG write, OpenFoundry write, or model promotion is performed here.")
        candidates = relationships.get("candidates") or []
        if candidates:
            rows = [
                {
                    "subject": c.get("subject"),
                    "predicate": c.get("predicate"),
                    "object": c.get("object"),
                    "status": c.get("status"),
                    "rationale": c.get("rationale"),
                }
                for c in candidates
            ]
            _display_df(
                rows,
                {
                    "subject": "Subject",
                    "predicate": "Relationship",
                    "object": "Object",
                    "status": "Review status",
                    "rationale": "Why it matters",
                },
            )
        if relationships.get("paths"):
            _df([{"artifact": k, "path": v} for k, v in relationships["paths"].items() if v])
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
    render_run_header(snapshot)
    rithmic_endpoint = (snapshot.system or {}).get("rithmic_endpoint") or {}
    if rithmic_endpoint:
        st.subheader("Rithmic Endpoint Status")
        endpoint_rows = [
            {
                "profile": rithmic_endpoint.get("profile"),
                "system": rithmic_endpoint.get("system"),
                "gateway": rithmic_endpoint.get("gateway"),
                "status": rithmic_endpoint.get("status"),
                "reason": rithmic_endpoint.get("reason_code"),
                "username_set": (rithmic_endpoint.get("credentials") or {}).get("username_set"),
                "password_set": (rithmic_endpoint.get("credentials") or {}).get("password_set"),
                "secret_exposed": rithmic_endpoint.get("secret_exposed", False),
                "config_path": rithmic_endpoint.get("config_path"),
            }
        ]
        _df(endpoint_rows)
    ibkr_endpoint = (snapshot.system or {}).get("ibkr_endpoint") or {}
    if ibkr_endpoint:
        st.subheader("IBKR Endpoint Status")
        endpoint_rows = [
            {
                "profile": ibkr_endpoint.get("profile"),
                "provider": ibkr_endpoint.get("provider"),
                "transport": ibkr_endpoint.get("transport"),
                "mode": ibkr_endpoint.get("mode"),
                "host": ibkr_endpoint.get("host"),
                "port": ibkr_endpoint.get("port"),
                "status": ibkr_endpoint.get("status"),
                "reason": ibkr_endpoint.get("reason_code"),
                "headless_required": ibkr_endpoint.get("headless_handshake_required"),
                "api_status": (ibkr_endpoint.get("api") or {}).get("api_client_status"),
                "pipeline_gate": ibkr_endpoint.get("pipeline_gate_status"),
                "live_routing_gate": ibkr_endpoint.get("live_routing_gate_status"),
                "account_id_set": (ibkr_endpoint.get("credentials") or {}).get("account_id_set"),
                "secret_exposed": ibkr_endpoint.get("secret_exposed", False),
                "config_path": ibkr_endpoint.get("config_path"),
                "runtime_status_path": ibkr_endpoint.get("runtime_status_path"),
            }
        ]
        _df(endpoint_rows)
        if ibkr_endpoint.get("blocking_gates"):
            _display_df(
                ibkr_endpoint["blocking_gates"],
                {
                    "gate": "Gate",
                    "status": "Status",
                    "reason": "Reason",
                },
            )
    lane_rows = ((snapshot.system or {}).get("lane_registry") or {}).get("rows") or []
    lane_registry = (snapshot.system or {}).get("lane_registry") or {}
    if lane_registry.get("status") == "BLOCKING":
        st.subheader("Lane Registry Blocker")
        st.error("Lane registry failure is a Workbench blocker, not an empty-state condition.")
        if lane_registry.get("errors"):
            _display_df(
                lane_registry["errors"],
                {
                    "lane": "Lane",
                    "stage": "Stage",
                    "status": "Status",
                    "error": "Error",
                },
            )
        if lane_registry.get("blocking_gates"):
            _display_df(
                lane_registry["blocking_gates"],
                {
                    "gate": "Gate",
                    "status": "Status",
                    "lane": "Lane",
                    "reason": "Reason",
                },
            )
    if lane_rows:
        st.subheader("Lane Registry")
        _display_df(
            lane_rows,
            {
                "lane": "Lane",
                "source": "Source",
                "capability": "Capability",
                "symbols": "Symbols",
                "test_paths": "Tests",
                "load_status": "Status",
            },
        )
    providers = (snapshot.system or {}).get("llm_providers") or {}
    if providers:
        st.subheader("Provider Status")
        rows = []
        for provider, payload in providers.items():
            rows.append(
                {
                    "provider": provider,
                    "configured": payload.get("configured", payload.get("present")),
                    "role": payload.get("role", ""),
                    "model": payload.get("model_target") or payload.get("model", ""),
                    "runtime_access": payload.get("runtime_access", ""),
                    "reason": payload.get("reason", ""),
                    "secret_exposed": payload.get("secret_exposed", False),
                }
            )
        _df(rows)
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
