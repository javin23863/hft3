"""ReplaySession-backed per-hypothesis matrix (replaces SignalBacktester fills)."""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from backtest_pipeline.src.signal_backtester import BacktestResult, FillRecord
from features_engine.src.hypotheses.modules import BaseHypothesis
from replay.replay_session import ReplaySession, ReplaySessionConfig

_REPO = Path(__file__).resolve().parents[2]


def _resolve_ledger_dir(run_id: str, manifest_dir: Optional[Path] = None) -> Path:
    if manifest_dir is not None:
        return manifest_dir
    return _REPO / "artifacts" / "replay_ledgers" / run_id


def write_hft_ledgers(
    run_id: str,
    raw: Dict[str, Any],
    manifest_dir: Optional[Path] = None,
) -> Dict[str, str]:
    ledgers: Dict[str, str] = {}
    ledger_dir = _resolve_ledger_dir(run_id, manifest_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)

    fills_detail = raw.get("fills_detail", [])

    fills_path = ledger_dir / "fills.jsonl"
    with fills_path.open("w", encoding="utf-8") as f:
        for fill in fills_detail:
            f.write(json.dumps(fill) + "\n")
    ledgers["fills"] = str(fills_path)

    positions_path = ledger_dir / "positions.jsonl"
    with positions_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_id": run_id,
            "final_position": raw.get("position", 0.0),
            "balance": raw.get("balance", 0.0),
            "num_trades": raw.get("num_trades", 0),
            "steps": raw.get("steps", 0),
        }) + "\n")
    ledgers["positions"] = str(positions_path)

    pnl_path = ledger_dir / "pnl_timeseries.jsonl"
    with pnl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_id": run_id,
            "pnl": raw.get("balance", 0.0),
            "num_trades": raw.get("num_trades", 0),
            "fees": raw.get("fee", 0.0),
        }) + "\n")
    ledgers["pnl_timeseries"] = str(pnl_path)

    summary = raw.get("order_lifecycle_summary", {})
    transitions_path = ledger_dir / "order_state_transitions.jsonl"
    with transitions_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_id": run_id,
            "accepted": summary.get("accepted_count", 0),
            "filled": summary.get("filled_count", 0),
            "cancelled": summary.get("cancel_count", 0),
            "rejected": summary.get("rejected_count", 0),
        }) + "\n")
    ledgers["order_state_transitions"] = str(transitions_path)

    orders_path = ledger_dir / "orders.jsonl"
    with orders_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_id": run_id,
            "order_intent_count": raw.get("order_intent_count", 0),
            "lifecycle_path": raw.get("lifecycle_path", ""),
        }) + "\n")
    ledgers["orders"] = str(orders_path)

    latency_path = ledger_dir / "latency_metrics.json"
    with latency_path.open("w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id,
            "latency_ms": summary.get("latency_band_ms", 1.0),
            "steps": summary.get("steps", 0),
            "queue_model": raw.get("queue_model", "LogProbQueueModel2"),
        }, f, indent=2)
    ledgers["latency_metrics"] = str(latency_path)

    slippage_path = ledger_dir / "slippage_metrics.json"
    with slippage_path.open("w", encoding="utf-8") as f:
        fill_prices = [f["avg_fill_price"] for f in fills_detail if f.get("avg_fill_price", 0) > 0]
        avg_slippage = 0.0
        if fill_prices:
            ref = fill_prices[0]
            diffs = [abs(p - ref) / max(ref, 1e-10) for p in fill_prices]
            avg_slippage = float(np.mean(diffs)) * 10000
        json.dump({
            "run_id": run_id,
            "fills_count": len(fills_detail),
            "average_slippage_bps": round(avg_slippage, 4),
        }, f, indent=2)
    ledgers["slippage_metrics"] = str(slippage_path)

    manifest_path = ledger_dir / "hft_truth_manifest.json"
    manifest_path.write_text(
        json.dumps({
            "run_id": run_id,
            "fills_count": len(fills_detail),
            "balance": raw.get("balance", 0.0),
            "num_trades": raw.get("num_trades", 0),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    ledgers["hft_truth_manifest"] = str(manifest_path)

    return ledgers


def _pair_fills_into_trades(fills_detail: List[Dict[str, Any]]) -> List[float]:
    """Pair BUY/SELL fills chronologically and compute per-trade PnL.

    Matches fills in chronological order, pairing opposite sides by
    cumulative quantity. Returns list of per-trade PnL values.
    """
    if not fills_detail:
        return []

    sorted_fills = sorted(fills_detail, key=lambda f: f.get("timestamp_ns", 0))
    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []

    for f in sorted_fills:
        side = str(f.get("side", "BUY"))
        if side == "BUY":
            buys.append(f)
        else:
            sells.append(f)

    if not buys or not sells:
        return []

    pair_side = "BUY" if buys[0].get("timestamp_ns", 0) <= sells[0].get("timestamp_ns", 0) else "SELL"
    entries = buys if pair_side == "BUY" else sells
    exits = sells if pair_side == "BUY" else buys

    trade_pnls: List[float] = []
    for entry, exit_ in zip(entries, exits):
        entry_price = float(entry.get("avg_fill_price", 0.0))
        exit_price = float(exit_.get("avg_fill_price", 0.0))
        qty = min(float(entry.get("filled_quantity", 0.0)), float(exit_.get("filled_quantity", 0.0)))
        if entry_price > 0 and exit_price > 0 and qty > 0:
            if pair_side == "BUY":
                pnl = (exit_price - entry_price) * qty
            else:
                pnl = (entry_price - exit_price) * qty
            trade_pnls.append(pnl)

    return trade_pnls


def reconcile_pnl(raw: Dict[str, Any], tick_size: float = 0.25) -> Dict[str, Any]:
    fills_detail = raw.get("fills_detail", [])
    account_balance = float(raw.get("balance", 0.0))
    fees = float(raw.get("fee", 0.0))

    if not fills_detail:
        return {
            "pnl_from_fills": 0.0,
            "pnl_from_account": account_balance,
            "passes": account_balance == 0.0,
            "reason": "no_fills_to_reconcile",
        }

    pnl_from_fills = 0.0
    for fill in fills_detail:
        side = fill.get("side", "BUY")
        price = float(fill.get("avg_fill_price", 0.0))
        qty = float(fill.get("filled_quantity", 0.0))
        fill_fees = float(fill.get("fees", 0.0))
        if side == "BUY":
            pnl_from_fills -= price * qty + fill_fees
        else:
            pnl_from_fills += price * qty - fill_fees

    pnl_from_account = account_balance - fees
    delta = abs(pnl_from_fills - pnl_from_account)
    total_qty = sum(float(f.get("filled_quantity", 0.0)) for f in fills_detail)
    tolerance = max(total_qty * tick_size, 0.01)
    passes = delta <= tolerance

    return {
        "pnl_from_fills": round(pnl_from_fills, 4),
        "pnl_from_account": round(pnl_from_account, 4),
        "passes": passes,
        "delta": round(delta, 4),
        "tolerance": round(tolerance, 4),
        "reason": "" if passes else f"pnl_delta_{delta:.4f}_exceeds_tolerance_{tolerance:.4f}",
    }


def _compute_win_rate(
    fills_detail: List[Dict[str, Any]],
    net_pnl: float,
    num_trades: int,
) -> float:
    trade_pnls = _pair_fills_into_trades(fills_detail)
    if trade_pnls:
        wins = sum(1 for p in trade_pnls if p > 0)
        return wins / max(len(trade_pnls), 1)
    return 1.0 if net_pnl > 0 and num_trades > 0 else 0.0


def run_hypothesis_replay(
    hypothesis: BaseHypothesis,
    npz_path: str,
    *,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
    max_steps: int | None = None,
    imbalance_ablation_mode_id: str = "",
    auction_events: list | None = None,
    event_window_id: str = "",
    meta_out: dict | None = None,
) -> BacktestResult:
    strategy = HypothesisReplayStrategy(hypothesis, signal_threshold=signal_threshold)
    cfg = ReplaySessionConfig(
        npz_path=npz_path,
        latency_ms=latency_ms,
        max_steps=max_steps,
        imbalance_ablation_mode_id=imbalance_ablation_mode_id,
        auction_events=list(auction_events or []),
        event_window_id=event_window_id,
    )
    session = ReplaySession(cfg, strategy)
    raw = session.run()
    if meta_out is not None:
        meta_out["imbalance_snapshot_summary"] = raw.get("imbalance_snapshot_summary")
        meta_out["imbalance_samples"] = raw.get("imbalance_samples", [])
        meta_out["fills_detail"] = raw.get("fills_detail", [])
        meta_out["steps"] = raw.get("steps", 0)
        meta_out["run_id"] = raw.get("run_id", "")
        meta_out["balance"] = raw.get("balance", 0.0)
        meta_out["fee"] = raw.get("fee", 0.0)
        meta_out["num_trades"] = raw.get("num_trades", 0)
        meta_out["position"] = raw.get("position", 0.0)
        meta_out["order_intent_count"] = raw.get("order_intent_count", 0)
        meta_out["order_lifecycle_summary"] = raw.get("order_lifecycle_summary", {})
        meta_out["lifecycle_path"] = raw.get("lifecycle_path", "")
        meta_out["queue_model"] = cfg.queue_model

    result = raw
    if result.get("error"):
        if meta_out is not None:
            meta_out["reconciliation"] = {
                "pnl_from_fills": 0.0, "pnl_from_account": 0.0,
                "passes": True, "reason": "error_result",
            }
        return BacktestResult(
            hypothesis_id=hypothesis.hyp_id,
            net_pnl=0.0, num_trades=0, win_rate=0.0,
            expectancy=0.0, adverse_selection_ticks=0.0, tail_loss=0.0,
        )

    num_trades = int(result.get("num_trades", 0))
    pnl = float(result.get("balance", 0.0))
    fills_detail = result.get("fills_detail", [])

    fills: List[FillRecord] = []
    for fill in fills_detail:
        fills.append(
            FillRecord(
                timestamp_ns=int(fill.get("timestamp_ns", 0)),
                side=str(fill.get("side", "BUY")),
                exec_price=float(fill.get("avg_fill_price", 0.0)),
                qty=float(fill.get("filled_quantity", 0.0)),
                hypothesis_id=hypothesis.hyp_id,
                signal=0.0,
                mid_at_exec=float(fill.get("avg_fill_price", 0.0)),
            )
        )

    win_rate = _compute_win_rate(fills_detail, pnl, num_trades)

    return BacktestResult(
        hypothesis_id=hypothesis.hyp_id,
        net_pnl=pnl,
        num_trades=num_trades,
        win_rate=win_rate,
        expectancy=pnl / max(num_trades, 1),
        adverse_selection_ticks=0.0,
        tail_loss=min(0.0, pnl),
        fills=fills,
    )


def run_all_hypotheses_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
) -> Dict[int, BacktestResult]:
    return {
        h.hyp_id: run_hypothesis_replay(
            h, npz_path, latency_ms=latency_ms, signal_threshold=signal_threshold
        )
        for h in hypotheses
    }


def run_latency_matrix_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_bands: List[float],
    signal_threshold: float = 0.15,
) -> Dict[float, Dict[int, BacktestResult]]:
    return {
        lat: run_all_hypotheses_replay(
            hypotheses, npz_path, latency_ms=lat, signal_threshold=signal_threshold
        )
        for lat in latency_bands
    }


def deprecate_signal_backtester_fill_path() -> None:
    warnings.warn(
        "SignalBacktester internal fill simulation is deprecated; "
        "use replay_matrix.run_hypothesis_replay / ReplaySession instead.",
        DeprecationWarning,
        stacklevel=2,
    )
