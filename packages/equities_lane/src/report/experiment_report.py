"""Experiment report writer with ablation and degraded assumptions."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.features.feature_registry import ablation_modules
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.types import UniverseConfig


def run_experiment(
    session_path: str,
    universe: UniverseConfig,
    reports_root: Path,
    *,
    ablation: str = "all",
) -> Path:
    run_id = f"equities_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = reports_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    bt = LowFloatBacktester(universe)
    meta, _ = load_session(session_path)
    allow_degraded = meta.degraded.degraded_mode
    baseline = bt.run(session_path, allow_degraded=allow_degraded)
    baseline_dict = baseline.to_dict()

    ablation_results: dict[str, Any] = {}
    modules = ablation_modules(universe) if ablation == "all" else [ablation]
    for mod in modules:
        ablated = bt.run(session_path, ablation=mod, allow_degraded=allow_degraded)
        ablation_results[mod] = {
            "net_pnl": ablated.net_pnl,
            "delta_pnl": ablated.net_pnl - baseline.net_pnl,
            "num_trades": ablated.num_trades,
        }

    summary = {
        "run_id": run_id,
        "net_pnl": baseline.net_pnl,
        "num_trades": baseline.num_trades,
        "max_drawdown": baseline.max_drawdown,
        "turnover": baseline.turnover,
        "tail_loss": baseline.tail_loss,
        "sharpe_proxy": _sharpe_proxy(baseline.net_pnl, baseline.max_drawdown),
    }

    robustness = _robustness_grid(session_path, universe)
    failure_cases = {
        "failure_notes": baseline.failure_notes,
        "tail_loss": baseline.tail_loss,
        "max_drawdown": baseline.max_drawdown,
    }
    degraded = {
        "degraded_mode": baseline.degraded.degraded_mode,
        "assumptions": baseline.degraded.assumptions,
    }

    (out_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(_config_snapshot(universe), sort_keys=False),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "ablation.json").write_text(json.dumps(ablation_results, indent=2), encoding="utf-8")
    (out_dir / "robustness.json").write_text(json.dumps(robustness, indent=2), encoding="utf-8")
    (out_dir / "failure_cases.json").write_text(
        json.dumps(failure_cases, indent=2), encoding="utf-8"
    )
    (out_dir / "degraded_assumptions.json").write_text(
        json.dumps(degraded, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_markdown_report(summary, ablation_results, degraded), encoding="utf-8")
    return out_dir


def _sharpe_proxy(pnl: float, max_dd: float) -> float:
    if max_dd <= 1e-9:
        return 0.0
    return pnl / max_dd


def _robustness_grid(session_path: str, universe: UniverseConfig) -> dict[str, Any]:
    grid: dict[str, Any] = {}
    bt = LowFloatBacktester(universe)
    for slip in (5.0, 10.0, 20.0):
        u = _clone_universe(universe, slippage_bps=slip)
        r = LowFloatBacktester(u).run(session_path)
        grid[f"slippage_bps_{slip}"] = r.net_pnl
    for lat in (1.0, 5.0, 15.0):
        u = _clone_universe(universe, latency_ms=lat)
        r = LowFloatBacktester(u).run(session_path)
        grid[f"latency_ms_{lat}"] = r.net_pnl
    _ = bt
    return grid


def _clone_universe(
    universe: UniverseConfig,
    *,
    slippage_bps: float | None = None,
    latency_ms: float | None = None,
) -> UniverseConfig:
    import copy

    u = copy.deepcopy(universe)
    if slippage_bps is not None:
        u.execution.slippage_bps = slippage_bps
    if latency_ms is not None:
        u.execution.latency_ms = latency_ms
    return u


def _config_snapshot(universe: UniverseConfig) -> dict[str, Any]:
    return {
        "filters": {
            "float_max_shares": universe.filters.float_max_shares,
            "min_premarket_gap_pct": universe.filters.min_premarket_gap_pct,
        },
        "features": {
            "ofi": universe.features.ofi,
            "vpin": universe.features.vpin,
            "hawkes": universe.features.hawkes,
            "hmm": universe.features.hmm,
        },
        "signals": {
            "min_mlofi_pc1": universe.signals.min_mlofi_pc1,
            "min_hmm_markup_prob": universe.signals.min_hmm_markup_prob,
        },
    }


def _markdown_report(
    summary: dict[str, Any],
    ablation: dict[str, Any],
    degraded: dict[str, Any],
) -> str:
    lines = [
        "# Low-float runner experiment",
        "",
        f"- Net PnL: {summary['net_pnl']:.2f}",
        f"- Trades: {summary['num_trades']}",
        f"- Max drawdown: {summary['max_drawdown']:.4f}",
        f"- Turnover: {summary['turnover']:.2f}",
        "",
        "## Ablation",
    ]
    for mod, row in ablation.items():
        lines.append(f"- {mod}: delta_pnl={row['delta_pnl']:.2f}")
    lines.extend(["", "## Degraded assumptions"])
    for a in degraded.get("assumptions", []):
        lines.append(f"- {a}")
    return "\n".join(lines) + "\n"
