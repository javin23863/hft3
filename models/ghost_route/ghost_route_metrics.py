"""Metrics and report helpers for Ghost Route backtests."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


LATENCY_SENSITIVITY_US = (10, 23, 50, 100, 250, 1000)


def summarize_event_log(rows: list[dict[str, Any]]) -> dict[str, Any]:
    signals = len(rows)
    fills = [row for row in rows if int(row.get("filled_quantity") or 0) > 0]
    full = [row for row in rows if row.get("fill_status") == "FULL_FILL"]
    partial = [row for row in rows if row.get("fill_status") == "PARTIAL_FILL"]
    misses = [row for row in rows if str(row.get("fill_status") or "").startswith("MISS_")]
    net_pnl = sum(float(row.get("net_pnl") or 0.0) for row in rows)
    fees = sum(float(row.get("fees") or 0.0) for row in rows)
    slippage = sum(float(row.get("slippage_cost") or 0.0) for row in rows)
    adverse = sum(float(row.get("adverse_selection_cost") or 0.0) for row in rows)
    lead_times = sorted(float(row.get("lead_time_us") or 0.0) for row in rows if row.get("lead_time_us") not in (None, ""))
    net_expectancy_per_signal = net_pnl / signals if signals else 0.0
    net_expectancy_per_fill = net_pnl / len(fills) if fills else 0.0
    classification = "FAIL"
    if signals == 0:
        classification = "WATCHLIST"
    elif net_expectancy_per_signal > 0 and len(fills) > 0:
        classification = "PASS"
    return {
        "classification": classification,
        "total_final_ghost_route_signals": signals,
        "full_fill_rate": len(full) / signals if signals else 0.0,
        "partial_fill_rate": len(partial) / signals if signals else 0.0,
        "miss_rate": len(misses) / signals if signals else 0.0,
        "average_signal_lead_time_us": sum(lead_times) / len(lead_times) if lead_times else 0.0,
        "median_signal_lead_time_us": lead_times[len(lead_times) // 2] if lead_times else 0.0,
        "net_expectancy_per_signal": net_expectancy_per_signal,
        "net_expectancy_per_filled_order": net_expectancy_per_fill,
        "gross_pnl": sum(float(row.get("gross_pnl") or 0.0) for row in rows),
        "net_pnl": net_pnl,
        "fees": fees,
        "slippage_cost": slippage,
        "adverse_selection_cost": adverse,
        "latency_sensitivity_us": list(LATENCY_SENSITIVITY_US),
        "acceptance_note": "Classification requires 23us latency, measured compute latency, fees, partial/missed fills, and adverse selection.",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    if not fieldnames:
        fieldnames = ["model", "classification", "reason"]
        rows = [{"model": "ghost_route", "classification": "WATCHLIST", "reason": "no_signals_observed"}]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Ghost Route Backtest Report",
        "",
        f"Classification: `{summary.get('classification', 'WATCHLIST')}`",
        "",
        "## Core Metrics",
        "",
    ]
    for key in (
        "total_final_ghost_route_signals",
        "full_fill_rate",
        "partial_fill_rate",
        "miss_rate",
        "average_signal_lead_time_us",
        "median_signal_lead_time_us",
        "net_expectancy_per_signal",
        "net_expectancy_per_filled_order",
        "gross_pnl",
        "net_pnl",
        "fees",
        "slippage_cost",
        "adverse_selection_cost",
    ):
        lines.append(f"- `{key}`: `{summary.get(key, '')}`")
    lines.extend(
        [
            "",
            "## Latency Sensitivity",
            "",
            "Required sensitivity bands: `10us`, `23us`, `50us`, `100us`, `250us`, `1ms`.",
            "",
            "A pass is not allowed unless the edge survives realistic 23us wire-to-wire latency plus measured compute latency.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
