"""Fixed report card schema and renderers for CHI404 latency probes."""
from __future__ import annotations

from typing import Any

REPORT_SCHEMA_VERSION = "1"


def _gate_status(ok: bool | None) -> str:
    if ok is True:
        return "PASS"
    if ok is False:
        return "FAIL"
    return "BLOCKED"


def _fmt_us(val: float | int | None) -> str:
    if val is None:
        return "n/a"
    return f"{float(val):.0f} us"


def _fmt_ms_from_us(val: float | int | None) -> str:
    if val is None:
        return "n/a"
    return f"{float(val) / 1000.0:.3f} ms"


def build_report_card(summary: dict[str, Any]) -> dict[str, Any]:
    """Build a fixed-shape report card from latency_summary payload."""
    gates = summary.get("gates") or {}
    rec = summary.get("recommended_lane") or {}
    appendix = summary.get("trial_order_ack_appendix") or {}
    cyclictest = summary.get("cyclictest") or {}
    network = summary.get("network") or {}

    cyclictest_pass = gates.get("cyclictest_pass")
    network_pass = gates.get("network_pass")
    infra_pass = cyclictest_pass is True and network_pass is True

    if cyclictest_pass is False or network_pass is False:
        overall = "FAIL"
    elif infra_pass:
        overall = "PARTIAL"
    else:
        overall = "PARTIAL"

    colo_gates: list[dict[str, Any]] = [
        {
            "id": "cyclictest_loaded",
            "label": "CPU jitter (loaded cyclictest p99)",
            "status": _gate_status(cyclictest_pass),
            "value": _fmt_us(cyclictest.get("max_p99_us")),
            "limit": _fmt_us(cyclictest.get("limit_us")),
            "detail": f"loaded_runs={cyclictest.get('loaded_runs', 0)} gate={cyclictest.get('gate_mode', 'loaded_only')}",
        },
        {
            "id": "network_worst",
            "label": "Network worst p99",
            "status": _gate_status(network_pass),
            "value": _fmt_us(summary.get("network_p99_us")),
            "limit": _fmt_us(summary.get("network_limit_us")),
            "detail": f"source={summary.get('network_p99_worst_source', 'n/a')}",
        },
        {
            "id": "order_ack_rapi",
            "label": "Order ack (R|API+ authoritative)",
            "status": "BLOCKED",
            "value": "not measured",
            "limit": "< 2 ms p99",
            "detail": summary.get("rithmic_app_latency", {}).get("reason", "R|API+ not wired"),
        },
    ]

    gw = network.get("gateway_ping") or {}
    rh_tcp = network.get("rithmic_tcp_65000") or {}

    def _probe_p99(block: dict[str, Any]) -> str:
        val = block.get("p99_ms")
        return f"{float(val):.3f} ms" if isinstance(val, (int, float)) else "n/a"

    network_probes: list[dict[str, Any]] = [
        {
            "probe": "gateway_ping",
            "host": gw.get("host"),
            "status": gw.get("status", "n/a"),
            "p99": _probe_p99(gw),
            "loss_pct": gw.get("loss_pct"),
        },
        {
            "probe": "rithmic_tcp_65000",
            "host": rh_tcp.get("host"),
            "status": rh_tcp.get("status", "n/a"),
            "p99": _probe_p99(rh_tcp),
            "loss_pct": None,
        },
    ]

    trial_status = str(appendix.get("status", "n/a")).upper()
    trial_row = {
        "label": "Trial order ack (R|Trader VM, non-production)",
        "status": trial_status,
        "value": (
            f"{appendix['order_ack_p99_ms']:.3f} ms p99"
            if appendix.get("order_ack_p99_ms") is not None
            else "not measured"
        ),
        "profile_path": appendix.get("profile_path"),
        "reason": appendix.get("reason"),
        "populate_hint": appendix.get("populate_hint"),
        "note": "Does not affect colo recommended_lane",
    }

    blocked = [
        {
            "id": "rithmic_app_latency",
            "status": summary.get("rithmic_app_latency", {}).get("status"),
            "reason": summary.get("rithmic_app_latency", {}).get("reason"),
        },
        {
            "id": "e2e_harness",
            "status": summary.get("e2e_harness", {}).get("status"),
            "reason": summary.get("e2e_harness", {}).get("reason"),
        },
    ]

    sg = summary.get("strategy_guidance") or {}

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "header": {
            "run_id": summary.get("run_id"),
            "timestamp_utc": summary.get("timestamp_utc"),
            "authoritative_source": summary.get("authoritative_source"),
            "raw_dir": summary.get("raw_dir"),
            "pass_criteria_path": summary.get("pass_criteria_path"),
        },
        "verdict": {
            "overall": overall,
            "dominant_bottleneck": summary.get("dominant_bottleneck"),
            "infra_pass": infra_pass,
            "probe_exit_nonzero": cyclictest_pass is False or network_pass is False,
        },
        "colo_gates": colo_gates,
        "colo_lane": {
            "lane": rec.get("lane"),
            "lane_name": rec.get("lane_name"),
            "partial": rec.get("partial"),
            "infrastructure_meets_lane_1": rec.get("infrastructure_meets_lane_1"),
            "note": rec.get("note"),
        },
        "network_probes": network_probes,
        "trial_appendix": trial_row,
        "blocked": blocked,
        "strategy": {
            "realistic": list(sg.get("realistic") or []),
            "unrealistic": list(sg.get("unrealistic") or []),
        },
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_markdown(summary: dict[str, Any], card: dict[str, Any]) -> str:
    """Render latency summary as a fixed-section markdown report."""
    h = card["header"]
    v = card["verdict"]
    lane = card["colo_lane"]
    trial = card["trial_appendix"]

    lines: list[str] = [
        "# CHI404 Latency Report",
        "",
        "## 1. Run",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Run ID | `{h.get('run_id')}` |",
        f"| UTC | {h.get('timestamp_utc')} |",
        f"| Source | {h.get('authoritative_source')} (no workstation metrics) |",
        f"| Raw data | `{h.get('raw_dir')}` |",
        f"| Pass criteria | `{h.get('pass_criteria_path')}` |",
        "",
        "## 2. Verdict",
        "",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| Overall | **{v.get('overall')}** |",
        f"| Dominant bottleneck | {v.get('dominant_bottleneck')} |",
        f"| Colo infra gates | {'PASS' if v.get('infra_pass') else 'FAIL'} |",
        "",
        "## 3. Colo gates (authoritative)",
        "",
    ]
    lines.extend(
        _md_table(
            ["Check", "Status", "Value", "Limit", "Detail"],
            [
                [
                    g["label"],
                    g["status"],
                    g["value"],
                    g["limit"],
                    g.get("detail", ""),
                ]
                for g in card["colo_gates"]
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 4. Colo lane",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Lane | {lane.get('lane', 'n/a')} ({lane.get('lane_name', 'n/a')}) |",
            f"| Partial | {'yes' if lane.get('partial') else 'no'} |",
            f"| Infra meets lane 1 | {'yes' if lane.get('infrastructure_meets_lane_1') else 'no'} |",
            f"| Note | {lane.get('note') or '—'} |",
            "",
            "## 5. Network probes (detail)",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ["Probe", "Host", "Status", "p99", "Loss %"],
            [
                [
                    p["probe"],
                    str(p.get("host") or "—"),
                    str(p.get("status") or "—"),
                    str(p.get("p99") or "—"),
                    str(p.get("loss_pct") if p.get("loss_pct") is not None else "—"),
                ]
                for p in card["network_probes"]
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 6. Trial order ack (non-production appendix)",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Status | **{trial.get('status')}** |",
            f"| Value | {trial.get('value')} |",
            f"| Profile | `{trial.get('profile_path') or '—'}` |",
            f"| Reason | {trial.get('reason') or '—'} |",
            f"| Populate | {trial.get('populate_hint') or '—'} |",
            f"| Note | {trial.get('note')} |",
            "",
            "## 7. Blocked (R|API+)",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ["Probe", "Status", "Reason"],
            [[b["id"], str(b.get("status")), str(b.get("reason") or "—")] for b in card["blocked"]],
        )
    )
    lines.extend(["", "## 8. Strategy guidance", "", "**Realistic**", ""])
    for item in card["strategy"]["realistic"]:
        lines.append(f"- {item}")
    lines.extend(["", "**Unrealistic**", ""])
    for item in card["strategy"]["unrealistic"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_console(card: dict[str, Any]) -> str:
    """Fixed-width console report (same sections every run)."""
    h = card["header"]
    v = card["verdict"]
    lane = card["colo_lane"]
    trial = card["trial_appendix"]
    width = 72
    bar = "=" * width
    dash = "-" * width

    lines = [
        bar,
        "CHI404 LATENCY REPORT".center(width),
        bar,
        f"Run ID   : {h.get('run_id')}",
        f"UTC      : {h.get('timestamp_utc')}",
        f"Source   : {h.get('authoritative_source')} (no workstation metrics)",
        dash,
        "VERDICT",
        f"  Overall            : {v.get('overall')}",
        f"  Dominant bottleneck: {v.get('dominant_bottleneck')}",
        f"  Colo infra gates   : {'PASS' if v.get('infra_pass') else 'FAIL'}",
        dash,
        "COLO GATES (authoritative)",
    ]
    for g in card["colo_gates"]:
        lines.append(
            f"  [{g['status']:7}] {g['label']}: {g['value']} (limit {g['limit']})"
        )
        if g.get("detail"):
            lines.append(f"           {g['detail']}")
    lines.extend(
        [
            dash,
            "COLO LANE",
            f"  Lane : {lane.get('lane', 'n/a')} ({lane.get('lane_name', 'n/a')})"
            f"  partial={'yes' if lane.get('partial') else 'no'}",
            f"  Note : {lane.get('note') or '—'}",
            dash,
            "TRIAL ORDER ACK (non-production appendix)",
            f"  Status : {trial.get('status')}",
            f"  Value  : {trial.get('value')}",
            f"  Profile: {trial.get('profile_path') or '—'}",
        ]
    )
    if trial.get("reason"):
        lines.append(f"  Reason : {trial['reason']}")
    if trial.get("populate_hint"):
        lines.append(f"  Action : {trial['populate_hint']}")
    lines.extend([dash, "BLOCKED (R|API+)",])
    for b in card["blocked"]:
        lines.append(f"  {b['id']}: {b.get('status')} — {b.get('reason')}")
    lines.extend([dash, "STRATEGY — realistic:",])
    for item in card["strategy"]["realistic"]:
        lines.append(f"  - {item}")
    lines.append("STRATEGY — unrealistic:")
    for item in card["strategy"]["unrealistic"]:
        lines.append(f"  - {item}")
    lines.append(bar)
    return "\n".join(lines)
