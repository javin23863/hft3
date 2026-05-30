"""Narrative run reports and unified research cards."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from features_engine.src.hypotheses.registry import HypothesisRegistry
from workbench.src.latency.viability import LatencyViability
from workbench.src.robustness.pack import RobustnessResult


def generate_hyp_research_card(hyp_id: int, results: dict) -> dict:
    return HypothesisRegistry().generate_research_card(hyp_id, results)


def generate_pdf_research_card(model_id: str, results: dict) -> dict:
    return {
        "model_id": model_id,
        "alpha_family_or_discovered_behavior": results.get("name", model_id),
        "net_pnl": results.get("net_pnl", 0.0),
        "num_trades": results.get("num_trades", 0),
        "approval_status": results.get("approval_status", "FAIL"),
        "latency_breakeven_ms": results.get("breakeven_ms"),
        "latency_buffer_ms": results.get("latency_buffer_ms"),
        "recommendation": results.get("recommendation", "REJECT"),
    }


def render_markdown_report(
    model_id: str,
    event_id: str,
    data_period: str,
    viability: LatencyViability,
    robustness: RobustnessResult,
    *,
    overfit_risk: Optional[str] = None,
) -> str:
    risk = overfit_risk or robustness.overfit_risk
    lines = [
        f"# Workbench Run Report: {model_id}",
        "",
        f"- **Event / period:** {event_id} ({data_period})",
        f"- **Latency authority:** C++ measured (Python runtime informational only)",
        "",
        "## Runtime (do not conflate)",
        "",
        f"- **Python research runtime:** {viability.python_research_runtime_us:.1f} µs (informational only)",
        f"- **C++ hot-path runtime (p99):** {viability.cpp_hot_path_runtime_us:.1f} µs (source of truth)",
        "",
        "## Latency viability",
        "",
        f"- **Measured production p99:** {viability.measured_production_p99_us:.1f} µs ({viability.measured_production_p99_ms:.4f} ms)",
        f"- **Break-even latency:** {viability.breakeven_us:.1f} µs ({viability.breakeven_ms:.4f} ms)",
        f"- **Latency profitability buffer:** {viability.latency_profitability_buffer_us:.1f} µs",
        f"- **Simulated latency-adjusted PnL:** ${viability.simulated_latency_adjusted_pnl:.2f}",
        f"- **Survives C++ execution delay:** {viability.survives_cpp_execution_delay}",
        f"- **Lane required / measured:** {viability.lane_required} / {viability.lane_measured}",
        f"- **Recommendation:** {viability.recommendation}",
        "",
        "## C++ latency profile (µs)",
        "",
    ]
    for k, v in viability.cpp_latency_profile.items():
        if k.endswith("_us") or "p50" in k or "p95" in k or "p99" in k:
            lines.append(f"- `{k}`: {v}")
    lines.extend([
        "",
        "## Robustness",
        "",
        f"Pack passed: **{robustness.passed}**",
        f"Over-fit risk: **{risk}**",
        "",
        "**Viability rule:** A strategy is viable only if it remains profitable after "
        "measured C++ hot-path latency, gateway latency, fill assumptions, slippage, fees, "
        "and adverse selection — not because Python backtest PnL is positive.",
        "",
    ])
    return "\n".join(lines)


def write_run_report(
    artifact_dir: Path,
    report: Dict[str, Any],
    markdown: str,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "diagnostics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (artifact_dir / "report.md").write_text(markdown, encoding="utf-8")
