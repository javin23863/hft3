"""Run PDF_MODEL_4 replay across defensive-layer ablation matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from backtest_pipeline.src.chi404_latency import (
    BACKTEST_LATENCY_NOTE,
    DEFAULT_CHI404_SUMMARY,
    resolve_replay_latency_ms,
)
from backtest_pipeline.src.pdf_defensive_config import DefensiveConfig, iter_ablation_configs
from backtest_pipeline.src.pdf_hybrid_strategy import HybridExecutionStrategy
from backtest_pipeline.src.runner import ReplayRunner
from features_engine.src.features.npz_feed import load_npz_events


def summarize_replay_result(
    result: Dict[str, Any],
    *,
    diagnostics: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Extract comparison metrics from ReplayRunner output.

    net_pnl and ending_balance are raw hftbacktest balance (before fee).
    Use net_pnl_after_fee for cross-mode PnL comparison.
    """
    if "error" in result:
        out: Dict[str, Any] = {
            "error": result["error"],
            "steps": result.get("steps", 0),
            "net_pnl": 0.0,
            "net_pnl_after_fee": 0.0,
            "num_trades": 0,
            "ending_balance": 0.0,
            "position": 0.0,
            "fee": 0.0,
        }
        if diagnostics:
            out.update(diagnostics)
        return out
    balance = float(result.get("balance", 0.0))
    fee = float(result.get("fee", 0.0))
    metrics: Dict[str, Any] = {
        "steps": int(result.get("steps", 0)),
        "net_pnl": balance,
        "net_pnl_after_fee": balance - fee,
        "num_trades": int(result.get("num_trades", 0)),
        "ending_balance": balance,
        "position": float(result.get("position", 0.0)),
        "fee": fee,
        "trading_volume": float(result.get("trading_volume", 0.0)),
    }
    if diagnostics:
        metrics.update(diagnostics)
    return metrics


def run_single_mode(
    *,
    npz_path: Path | str,
    event_meta: dict,
    defensive: DefensiveConfig,
    tick_size: float,
    latency_ms: float,
    queue_model: str,
    step_ns: int,
    max_steps: Optional[int] = None,
) -> Dict[str, Any]:
    strategy = HybridExecutionStrategy(
        str(npz_path),
        tick_size=tick_size,
        latency_ms=latency_ms,
        event_meta=event_meta,
        defensive=defensive,
    )
    runner = ReplayRunner(str(npz_path), tick_size=tick_size)
    raw_result = runner.run_replay(
        model_logic_callback=strategy.on_step,
        latency_ms=latency_ms,
        queue_model=queue_model,
        step_ns=step_ns,
        use_combined_strategy=False,
        max_steps=max_steps,
    )
    metrics = summarize_replay_result(raw_result, diagnostics=strategy.diagnostics)
    return {
        "mode_id": defensive.mode_id,
        "description": defensive.description,
        "use_ofi": defensive.use_ofi,
        "use_vpin": defensive.use_vpin,
        "metrics": metrics,
        "result": raw_result,
    }


def run_defensive_ablation_matrix(
    *,
    npz_path: Path | str,
    event_meta: dict,
    tick_size: float = 0.25,
    latency_ms: float | None = None,
    chi404_summary: Path = DEFAULT_CHI404_SUMMARY,
    queue_model: str = "LogProbQueueModel2",
    step_ns: int = 100_000,
    configs: Optional[List[DefensiveConfig]] = None,
    max_steps: Optional[int] = None,
) -> Dict[str, Any]:
    """Run all flag combinations; fresh strategy/models per mode."""
    resolved_ms, latency_source, chi404_meta = resolve_replay_latency_ms(
        latency_ms=latency_ms,
        chi404_summary=chi404_summary,
    )
    npz_path = Path(npz_path)
    raw = load_npz_events(str(npz_path))
    matrix: List[Dict[str, Any]] = []
    for cfg in configs or list(iter_ablation_configs()):
        matrix.append(
            run_single_mode(
                npz_path=npz_path,
                event_meta=event_meta,
                defensive=cfg,
                tick_size=tick_size,
                latency_ms=resolved_ms,
                queue_model=queue_model,
                step_ns=step_ns,
                max_steps=max_steps,
            )
        )
    return {
        "model_id": "PDF_MODEL_4",
        "engine": "pdf_hybrid_ablation",
        "eval_scope": (
            "discovery_diagnostic_single_event; "
            "OFI/AS mid from hbt.depth BBO; VPIN from MBO TRADE sync (internal book mid, "
            "hbt.depth fallback when book empty); vpin_only uses unit OFI probe not book OFI"
        ),
        "metrics_note": "Prefer net_pnl_after_fee; net_pnl is ending balance before fee.",
        "event_id": event_meta.get("event_id"),
        "events": len(raw),
        "latency_ms": resolved_ms,
        "latency_source": latency_source,
        "backtest_latency_note": BACKTEST_LATENCY_NOTE,
        "chi404_measured_speed": chi404_meta,
        "queue_model": queue_model,
        "step_ns": step_ns,
        "max_steps": max_steps,
        "modes": matrix,
    }
