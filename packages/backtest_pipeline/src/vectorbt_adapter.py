"""VectorBT adapter — cheap hypothesis/parameter filter using vectorized backtesting.

Consumes HFT3 CandidateModel objects + data from the existing pipeline.
Produces FilterResult with promoted/rejected lists. Every promoted candidate
carries full traceable metadata (PromotedCandidate) for the promotion gate.

VectorBT is optional. If not installed, the adapter falls back to the
numpy signal-return simulator in this module and still applies the same
promotion gate. Missing data or missing signal bindings reject candidates;
they are never counted as a filtered pass.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np

from backtest_pipeline.src.promotion_gate import (
    PromotedCandidate,
    PromotionGate,
    RejectedCandidate,
    serialize_promoted,
)
from research_pipeline.types import CandidateModel, ParsedHypothesis

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[3]

_has_vectorbt: Optional[bool] = None


def _vectorbt_available() -> bool:
    global _has_vectorbt
    if _has_vectorbt is None:
        try:
            import vectorbt  # type: ignore
            _has_vectorbt = hasattr(vectorbt, "__version__")
        except ImportError:
            _has_vectorbt = False
    return _has_vectorbt


DEFAULT_PARAM_GRID = {
    "signal_threshold": [0.10, 0.15, 0.20, 0.25],
    "holding_period_bars": [5, 15, 30, 60],
    "stop_loss_pct": [None, 0.5, 1.0, 2.0],
    "take_profit_pct": [None, 0.5, 1.0, 2.0],
}


def _grid_size(grid: Dict[str, List[Any]]) -> int:
    total = 1
    for vals in grid.values():
        total *= len(vals)
    return total


@dataclass
class FilterResult:
    promoted: List[PromotedCandidate] = field(default_factory=list)
    rejected: List[RejectedCandidate] = field(default_factory=list)
    vectorbt_available: bool = False
    backend: str = ""
    run_id: str = ""
    total_candidates: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "vectorbt_available": self.vectorbt_available,
            "backend": self.backend,
            "total_candidates": self.total_candidates,
            "promoted_count": len(self.promoted),
            "rejected_count": len(self.rejected),
            "promoted": [p.to_dict() for p in self.promoted],
            "rejected": [r.to_dict() for r in self.rejected],
        }


def _default_data_loader(
    event_id: str,
    repo_root: Path,
) -> Optional[np.ndarray]:
    """Load OHLCV bars from existing HFT3 data pipeline.
    Falls back to building bars from the NPZ MBO data.
    Returns None if no data is available.
    """
    npz_dir = repo_root / "data" / "npz"
    candidates = list(npz_dir.glob(f"*{event_id}*_mbo.npz")) if npz_dir.exists() else []
    candidates.extend(list(repo_root.glob(f"data/npz/*{event_id}*_mbo.npz")))
    if not candidates:
        return None
    npz_path = str(candidates[0])
    try:
        from features_engine.src.features.npz_feed import load_npz_events
        raw = load_npz_events(npz_path)
        if len(raw) < 2:
            return None
        ts = raw["local_ts"].astype(np.int64)
        px = raw["px"].astype(np.float64)
        qty = raw["qty"].astype(np.float64)
        side_flags = raw["ev"].astype(np.int64)
        buy_mask = (side_flags & 0x1) == 1

        bar_interval_ns = 60_000_000_000
        start_ts = ts[0]
        end_ts = ts[-1]
        n_bars = max(1, int((end_ts - start_ts) / bar_interval_ns))
        o = np.zeros(n_bars)
        h = np.zeros(n_bars)
        l = np.full(n_bars, np.inf)
        c = np.zeros(n_bars)
        v = np.zeros(n_bars)

        for i in range(n_bars):
            bar_start = start_ts + i * bar_interval_ns
            bar_end = bar_start + bar_interval_ns
            mask = (ts >= bar_start) & (ts < bar_end)
            if not mask.any():
                o[i] = c[i - 1] if i > 0 else px[0]
                h[i] = o[i]
                l[i] = o[i]
                c[i] = o[i]
                continue
            idx = np.where(mask)[0]
            bar_px = px[idx]
            bar_qty = qty[idx]
            bar_buy = buy_mask[idx]
            o[i] = bar_px[0]
            h[i] = bar_px.max()
            l[i] = bar_px.min()
            c[i] = bar_px[-1]
            v[i] = bar_qty.sum()

        l[l == np.inf] = o[l == np.inf]
        return np.column_stack([o, h, l, c, v])
    except Exception as exc:
        print(f"Warning: NPZ data load failed: {exc}", file=sys.stderr)
        return None


def _compute_metrics_for_params(
    ohlcv: np.ndarray,
    entry_signal: np.ndarray,
    exit_signal: np.ndarray,
    stop_loss_pct: Optional[float],
    take_profit_pct: Optional[float],
    holding_period_bars: Optional[int] = None,
    price_col: int = 3,
    start_index: int = 1,
    end_index: Optional[int] = None,
) -> Dict[str, Any]:
    close = ohlcv[:, price_col]
    n = len(close)
    if n == 0:
        return {
            "net_return_pct": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.0,
            "num_trades": 0,
            "max_drawdown_pct": 0.0,
        }
    start = max(1, min(int(start_index), n - 1))
    end = n if end_index is None else max(start + 1, min(int(end_index), n))
    position = 0.0
    entry_price = 0.0
    trades: List[float] = []
    cum_return = 0.0
    peak = 0.0
    max_dd = 0.0
    entry_index = 0

    for i in range(1, start):
        if position != 0:
            ret = (close[i] - entry_price) / entry_price
            hit_stop = stop_loss_pct is not None and ret < -stop_loss_pct / 100.0
            hit_target = take_profit_pct is not None and ret > take_profit_pct / 100.0
            hit_holding_period = holding_period_bars is not None and i - entry_index >= holding_period_bars
            if hit_stop or hit_target or hit_holding_period or (position > 0 and exit_signal[i] < 0):
                entry_price = 0.0
                position = 0.0
                continue
        if position == 0 and entry_signal[i] > 0:
            entry_price = close[i]
            entry_index = i
            position = 1.0

    if position != 0:
        entry_price = close[start - 1]
        entry_index = min(entry_index, start - 1)

    for i in range(start, end):
        if position != 0:
            ret = (close[i] - entry_price) / entry_price
            hit_stop = stop_loss_pct is not None and ret < -stop_loss_pct / 100.0
            hit_target = take_profit_pct is not None and ret > take_profit_pct / 100.0
            hit_holding_period = holding_period_bars is not None and i - entry_index >= holding_period_bars
            if hit_stop:
                exit_return = -stop_loss_pct / 100.0
            elif hit_target:
                exit_return = take_profit_pct / 100.0
            else:
                exit_return = None
            if exit_return is not None:
                trades.append(exit_return)
                cum_return += exit_return
                entry_price = 0.0
                position = 0.0
                peak = max(peak, cum_return)
                max_dd = max(max_dd, peak - cum_return)
                continue

            if position > 0:
                unrealized = (close[i] - entry_price) / entry_price
            else:
                unrealized = (entry_price - close[i]) / entry_price
            cum_return_unrealized = cum_return + unrealized
            peak = max(peak, cum_return_unrealized)
            max_dd = max(max_dd, peak - cum_return_unrealized)

        if position == 0 and entry_signal[i] > 0:
            entry_price = close[i]
            entry_index = i
            position = 1.0
        elif position > 0 and (exit_signal[i] < 0 or hit_holding_period):
            trade_return = (close[i] - entry_price) / entry_price
            trades.append(trade_return)
            cum_return += trade_return
            entry_price = 0.0
            position = 0.0

    if position != 0:
        trade_return = (close[end - 1] - entry_price) / entry_price
        trades.append(trade_return)
        cum_return += trade_return

    n_trades = len([t for t in trades if abs(t) > 1e-12])
    expectancy = float(np.mean(trades)) if n_trades > 0 else 0.0
    win_rate = float(np.mean([t > 0 for t in trades])) if n_trades > 0 else 0.0
    total_return = float(cum_return)
    return {
        "net_return_pct": round(total_return * 100, 4),
        "expectancy": round(expectancy, 6),
        "win_rate": round(win_rate, 4),
        "num_trades": n_trades,
        "max_drawdown_pct": -round(max_dd * 100, 4),
    }


def _simulate_walk_forward(
    ohlcv: np.ndarray,
    entry_signal: np.ndarray,
    exit_signal: np.ndarray,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    holding_period_bars: Optional[int] = None,
    n_windows: int = 4,
    train_ratio: float = 0.6,
) -> Dict[str, Any]:
    n = len(ohlcv)
    if n < 20:
        return {"wf_consistency": 0.0, "oos_expectancy": 0.0}

    oos_expectancies: List[float] = []
    first_oos = int(n * train_ratio)
    if first_oos < 10 or first_oos >= n - 5:
        return {"wf_consistency": 0.0, "oos_expectancy": 0.0}
    bounds = np.linspace(first_oos, n, n_windows + 1, dtype=int)
    for start, end in zip(bounds[:-1], bounds[1:]):
        if end - start < 5:
            continue
        metrics_oos = _compute_metrics_for_params(
            ohlcv,
            entry_signal,
            exit_signal,
            stop_loss_pct,
            take_profit_pct,
            holding_period_bars=holding_period_bars,
            start_index=int(start),
            end_index=int(end),
        )
        oos_expectancies.append(float(metrics_oos["expectancy"]) if metrics_oos["num_trades"] > 0 else 0.0)

    consistency = 0.0
    if oos_expectancies:
        positive = sum(1 for e in oos_expectancies if e > 0)
        consistency = positive / len(oos_expectancies)
    oos_exp = float(np.mean(oos_expectancies)) if oos_expectancies else 0.0
    return {
        "wf_consistency": round(consistency, 4),
        "oos_expectancy": round(oos_exp, 6),
    }


def _default_signal_computer(
    cand: CandidateModel,
    ohlcv: np.ndarray,
    parsed: ParsedHypothesis,
    repo_root: Path,
) -> Tuple[np.ndarray, np.ndarray]:
    from features_engine.src.model_registry import resolve_model_id
    from features_engine.src.hypotheses.registry import get_active_hypotheses
    from features_engine.src.market_state_pipeline import MarketStatePipeline

    resolved = resolve_model_id(cand.model_id)
    hypotheses = get_active_hypotheses()
    hypothesis_cls = hypotheses.get(resolved)
    if hypothesis_cls is None:
        raise ValueError(f"model_id {cand.model_id} not in active hypotheses")

    pipeline = MarketStatePipeline()
    n_bars = len(ohlcv)
    signal = np.zeros(n_bars)

    for i in range(n_bars):
        bar_end_ts = int(ohlcv[i, 0] * 1_000_000_000) if ohlcv[i, 0] < 1e12 else int(ohlcv[i, 0])
        pipeline.process_event({"local_ts": bar_end_ts, "close": ohlcv[i, 3]})
        state = pipeline.latest_state
        if state is not None:
            sig = hypothesis_cls.evaluate(state)
            signal[i] = sig

    threshold = abs(float(cand.strategy_params.get("signal_threshold", 0.0)))
    entry_signal = np.where(signal > threshold, 1.0, 0.0)
    exit_signal = np.where(signal < -threshold, -1.0, 0.0)
    return entry_signal, exit_signal


def _grid_iter(grid: Dict[str, List[Any]]) -> Iterator[Dict[str, Any]]:
    keys = list(grid.keys())
    for values in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, values))


def _run_vectorbt_simulation(
    ohlcv: np.ndarray,
    candidates: List[CandidateModel],
    parsed: ParsedHypothesis,
    grid: Dict[str, List[Any]],
    repo_root: Path,
    signal_computer: Optional[Callable] = None,
) -> FilterResult:
    """Run VectorBT simulation when the library is available.
    Falls back to numpy-based simulation if VectorBT is not installed.

    ``parsed`` is used by the ``signal_computer`` to configure feature parameters.
    The actual usage happens inside ``signal_computer``, not directly in this function.
    """
    from backtest_pipeline.src.asset_class_routing import resolve_validation_path

    signal_computer = signal_computer or _default_signal_computer

    vbt = None
    vectorbt_available = _vectorbt_available()
    if vectorbt_available:
        import vectorbt as vbt  # type: ignore[no-redef]
    close = ohlcv[:, 3]
    open_p = ohlcv[:, 0]
    high = ohlcv[:, 1]
    low = ohlcv[:, 2]
    volume = ohlcv[:, 4]

    result = FilterResult(
        vectorbt_available=vectorbt_available,
        backend="vectorbt" if vectorbt_available else "numpy_fallback",
        run_id=(
            f"vbt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            if vectorbt_available
            else f"np_vbt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        ),
        total_candidates=len(candidates) * _grid_size(grid),
    )

    for cand in candidates:
        for params in _grid_iter(grid):
            merged = dict(cand.strategy_params)
            merged.update(params)
            signal_thresh = float(merged.get("signal_threshold", 0.15))
            holding_period = int(merged.get("holding_period_bars", 15))
            stop_loss = merged.get("stop_loss_pct")
            take_profit = merged.get("take_profit_pct")
            stop_loss_f = float(stop_loss) if stop_loss is not None else None
            take_profit_f = float(take_profit) if take_profit is not None else None
            grid_candidate = copy.copy(cand)
            grid_candidate.strategy_params = merged

            try:
                raw_entry_signal, raw_exit_signal = signal_computer(grid_candidate, ohlcv, parsed, repo_root)
            except Exception as exc:
                print(f"Warning: signal computer failed for {cand.candidate_id}: {exc}", file=sys.stderr)
                result.rejected.append(RejectedCandidate(
                    candidate_id=cand.candidate_id,
                    hypothesis_id=cand.model_id,
                    reject_reason="unresolvable_model_id",
                    metric_values={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                ))
                break

            entry_signal = np.where(np.asarray(raw_entry_signal, dtype=float) > signal_thresh, 1.0, 0.0)
            exit_signal = np.where(np.asarray(raw_exit_signal, dtype=float) < -signal_thresh, -1.0, 0.0)

            vbt_stats = {}
            if vbt is not None:
                try:
                    entries = entry_signal > 0
                    exits = exit_signal < 0
                    pf = vbt.Portfolio.from_signals(
                        close, entries=entries, exits=exits,
                        init_cash=10000.0, freq="1min",
                        sl_stop=stop_loss_f / 100.0 if stop_loss_f else None,
                        tp_stop=take_profit_f / 100.0 if take_profit_f else None,
                    )
                    vbt_stats = dict(pf.stats())
                except Exception as exc:
                    print(f"Warning: VectorBT portfolio sim failed for {cand.candidate_id}: {exc}", file=sys.stderr)
            else:
                vbt_stats = {
                    "backend": "numpy_fallback",
                    "reason": "vectorbt package unavailable; using numpy signal-return simulator",
                }

            metrics = _compute_metrics_for_params(
                ohlcv,
                entry_signal,
                exit_signal,
                stop_loss_f,
                take_profit_f,
                holding_period_bars=holding_period,
            )
            wf = _simulate_walk_forward(
                ohlcv,
                entry_signal,
                exit_signal,
                stop_loss_pct=stop_loss_f,
                take_profit_pct=take_profit_f,
                holding_period_bars=holding_period,
            )

            cand_id = _candidate_id(cand, merged)
            vectorbt_results = {
                "evidence_scope": "vectorbt_parameter_prefilter",
                "promotion_next_step": "hft_backtester_required",
                "signal_threshold": signal_thresh,
                "holding_period_bars": holding_period,
                "stop_loss_pct": stop_loss_f,
                "take_profit_pct": take_profit_f,
                "vbt_stats": vbt_stats,
                "filter_backend": result.backend,
                **metrics,
                **wf,
            }

            candidate_path = resolve_validation_path(cand)
            promoted = PromotedCandidate(
                candidate_id=cand_id,
                hypothesis_id=cand.model_id,
                strategy_family=cand.metadata.get("strategy_family", cand.model_id),
                asset_class=candidate_path.asset_class,
                symbol=candidate_path.symbol,
                timeframe="1m",
                param_values=merged,
                vectorbt_run_id=result.run_id,
                vectorbt_results=vectorbt_results,
                pass_reason="vectorbt_simulated",
                in_sample_results={"expectancy": metrics.get("expectancy", 0.0)},
                out_of_sample_results={"expectancy": wf.get("oos_expectancy", 0.0)},
            )
            result.promoted.append(promoted)

    return result


def _candidate_id(cand: CandidateModel, params: Dict[str, Any]) -> str:
    raw = f"{cand.model_id}_{json.dumps(params, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _resolve_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO, stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception as exc:
        print(f"Warning: git commit resolution failed: {exc}", file=sys.stderr)
        return ""


def filter_candidates(
    candidates: List[CandidateModel],
    parsed: ParsedHypothesis,
    event_id: str,
    repo_root: Optional[Path] = None,
    gates: Optional[PromotionGate] = None,
    param_grid: Optional[Dict[str, List[Any]]] = None,
    data_loader: Optional[Callable[[str, Path], Optional[np.ndarray]]] = None,
    signal_computer: Optional[Callable] = None,
    persist_promotions: bool = False,
) -> FilterResult:
    """Run VectorBT filter on candidates. Returns promoted+rejected lists.

    If VectorBT is not installed, this still runs the numpy fallback simulator
    with the same data, signal computer, and promotion gate. Missing OHLCV data
    rejects candidates; production promotion artifacts are only written when
    the caller explicitly passes persist_promotions=True.
    """
    gates = gates or PromotionGate()
    repo_root = repo_root or _REPO
    data_loader = data_loader or _default_data_loader
    grid = param_grid or DEFAULT_PARAM_GRID
    signal_computer = signal_computer or _default_signal_computer

    ohlcv = data_loader(event_id, repo_root)
    if ohlcv is None:
        logger.warning("No OHLCV data for %s — rejecting all candidates", event_id)
        result = FilterResult(
            vectorbt_available=_vectorbt_available(),
            backend="no_ohlcv_data",
            run_id=f"no_data_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            total_candidates=len(candidates),
        )
        ignored_escape = os.environ.get("HFT3_ALLOW_UNFILTERED", "").lower() in ("1", "true")
        for cand in candidates:
            result.rejected.append(RejectedCandidate(
                candidate_id=cand.candidate_id,
                hypothesis_id=cand.model_id,
                reject_reason="no_ohlcv_data",
                metric_values={"operator_escape_ignored": ignored_escape} if ignored_escape else {},
            ))
        return result

    result = _run_vectorbt_simulation(ohlcv, candidates, parsed, grid, repo_root, signal_computer)
    promoted_out: List[PromotedCandidate] = []
    rejected_out: List[RejectedCandidate] = list(result.rejected)

    git_commit = _resolve_git_commit()

    for prom in result.promoted:
        prom.git_commit = git_commit
        prom.config_path = str(
            repo_root / "packages" / "features_engine" / "config" / "model_registry.yaml"
        )
        prom.seed = 42
        prom.timestamp_utc = datetime.now(timezone.utc).isoformat()

        gate_pass = gates.evaluate(prom)
        if gate_pass:
            prom.pass_reason = "vectorbt_prefilter_passed"
            prom.in_sample_results["gate_pass"] = True
            if persist_promotions:
                serialize_promoted(prom, repo_root / "research_cards" / "promotion")
            promoted_out.append(prom)
        else:
            rejected_out.append(RejectedCandidate(
                candidate_id=prom.candidate_id,
                hypothesis_id=prom.hypothesis_id,
                reject_reason="promotion_gate_failed",
                metric_values=prom.vectorbt_results,
            ))

    result.promoted = promoted_out
    result.rejected = rejected_out
    return result


def load_validation_path(cand: CandidateModel) -> Any:
    from backtest_pipeline.src.asset_class_routing import resolve_validation_path
    return resolve_validation_path(cand)
