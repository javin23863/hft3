"""
Event-accurate hypothesis backtest: NPZ MBO -> MarketStatePipeline -> per-hypothesis PnL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from backtest_pipeline.src.fee_model import FeeModel
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from features_engine.src.hypotheses.modules import BaseHypothesis
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

TICK_VALUE_MES = 1.25  # $ per tick for MES


@dataclass
class FillRecord:
    timestamp_ns: int
    side: str
    exec_price: float
    qty: int
    hypothesis_id: int
    signal: float
    mid_at_exec: float


@dataclass
class BacktestResult:
    hypothesis_id: int
    net_pnl: float
    num_trades: int
    win_rate: float
    expectancy: float
    adverse_selection_ticks: float
    tail_loss: float
    fills: List[FillRecord] = field(default_factory=list)


@dataclass
class _HypSimState:
    position: int = 0
    entry_price: float = 0.0
    pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    fills: List[FillRecord] = field(default_factory=list)
    markouts: List[float] = field(default_factory=list)


def _future_mid(raw: np.ndarray, ts_ns: int, latency_ms: float) -> float:
    target = ts_ns + int(latency_ms * 1_000_000)
    i = int(np.searchsorted(raw["local_ts"], target, side="left"))
    i = min(i, len(raw) - 1)
    return float(raw[i]["px"])


def _apply_signal(
    sim: _HypSimState,
    hyp: BaseHypothesis,
    sig: float,
    mid: float,
    mbo,
    raw: np.ndarray,
    latency_ms: float,
    threshold: float,
    tick_size: float,
    tick_value: float,
    fee_model: FeeModel,
    max_position: int = 1,
) -> None:
    if sig > threshold and sim.position <= 0:
        if sim.position < 0:
            exit_px = _future_mid(raw, mbo.timestamp_ns, latency_ms)
            trade_pnl = (sim.entry_price - exit_px) / tick_size * tick_value
            trade_pnl -= fee_model.calculate_trade_cost(1, is_market_order=True)
            sim.pnl += trade_pnl
            sim.fills.append(
                FillRecord(mbo.timestamp_ns, "BUY", exit_px, 1, hyp.hyp_id, sig, mid)
            )
            if trade_pnl > 0:
                sim.wins += 1
            else:
                sim.losses += 1
        sim.entry_price = _future_mid(raw, mbo.timestamp_ns, latency_ms)
        sim.position = max_position
        sim.fills.append(
            FillRecord(mbo.timestamp_ns, "BUY", sim.entry_price, 1, hyp.hyp_id, sig, mid)
        )
        sim.pnl -= fee_model.calculate_trade_cost(1, is_market_order=True)

    elif sig < -threshold and sim.position >= 0:
        if sim.position > 0:
            exit_px = _future_mid(raw, mbo.timestamp_ns, latency_ms)
            trade_pnl = (exit_px - sim.entry_price) / tick_size * tick_value
            trade_pnl -= fee_model.calculate_trade_cost(1, is_market_order=True)
            sim.pnl += trade_pnl
            sim.fills.append(
                FillRecord(mbo.timestamp_ns, "SELL", exit_px, 1, hyp.hyp_id, sig, mid)
            )
            if trade_pnl > 0:
                sim.wins += 1
            else:
                sim.losses += 1
        sim.entry_price = _future_mid(raw, mbo.timestamp_ns, latency_ms)
        sim.position = -max_position
        sim.fills.append(
            FillRecord(mbo.timestamp_ns, "SELL", sim.entry_price, 1, hyp.hyp_id, sig, mid)
        )
        sim.pnl -= fee_model.calculate_trade_cost(1, is_market_order=True)


class SignalBacktester:
    def __init__(
        self,
        tick_size: float = 0.25,
        signal_threshold: float = 0.15,
        product: str = "MES",
    ):
        self.tick_size = tick_size
        self.signal_threshold = signal_threshold
        self.fee_model = FeeModel(product=product)
        self.tick_value = TICK_VALUE_MES

    def _finalize(self, sim: _HypSimState, hyp_id: int, raw: np.ndarray) -> BacktestResult:
        if sim.position != 0 and len(raw) > 0:
            exit_px = float(raw[-1]["px"])
            if sim.position > 0:
                sim.pnl += (exit_px - sim.entry_price) / self.tick_size * self.tick_value
            else:
                sim.pnl += (sim.entry_price - exit_px) / self.tick_size * self.tick_value
            sim.pnl -= self.fee_model.calculate_trade_cost(1, is_market_order=True)

        n_trades = sim.wins + sim.losses
        return BacktestResult(
            hypothesis_id=hyp_id,
            net_pnl=sim.pnl,
            num_trades=n_trades,
            win_rate=sim.wins / n_trades if n_trades else 0.0,
            expectancy=sim.pnl / n_trades if n_trades else 0.0,
            adverse_selection_ticks=float(np.mean(sim.markouts)) if sim.markouts else 0.0,
            tail_loss=float(np.percentile([sim.pnl], 5)) if n_trades else 0.0,
            fills=sim.fills,
        )

    def run_hypothesis(
        self,
        hypothesis: BaseHypothesis,
        raw_events: np.ndarray,
        latency_ms: float = 1.0,
        max_position: int = 1,
    ) -> BacktestResult:
        return self.run_all_hypotheses([hypothesis], raw_events, latency_ms, max_position)[
            hypothesis.hyp_id
        ]

    def run_all_hypotheses(
        self,
        hypotheses: List[BaseHypothesis],
        raw_events: np.ndarray,
        latency_ms: float = 1.0,
        max_position: int = 1,
    ) -> Dict[int, BacktestResult]:
        """Single MBO pass: shared pipeline, all hypotheses evaluated per event."""
        pipeline = MarketStatePipeline(tick_size=self.tick_size, latency_ms=latency_ms)
        sims = {h.hyp_id: _HypSimState() for h in hypotheses}

        for mbo in iter_mbo_events(raw_events):
            state = pipeline.process_event(mbo)
            mid = state.f("mid_price", 0.0)
            if mid <= 0:
                continue

            for hyp in hypotheses:
                sim = sims[hyp.hyp_id]
                sig = hyp.evaluate(state)
                _apply_signal(
                    sim,
                    hyp,
                    sig,
                    mid,
                    mbo,
                    raw_events,
                    latency_ms,
                    self.signal_threshold,
                    self.tick_size,
                    self.tick_value,
                    self.fee_model,
                    max_position,
                )
                if sim.fills and mbo.action == "TRADE":
                    last = sim.fills[-1]
                    m100 = _future_mid(raw_events, last.timestamp_ns, 0.1)
                    if last.side == "BUY":
                        sim.markouts.append((m100 - last.exec_price) / self.tick_size)
                    else:
                        sim.markouts.append((last.exec_price - m100) / self.tick_size)

        return {
            h.hyp_id: self._finalize(sims[h.hyp_id], h.hyp_id, raw_events) for h in hypotheses
        }

    def run_latency_matrix(
        self,
        hypotheses: List[BaseHypothesis],
        raw_events: np.ndarray,
        latency_bands: List[float],
    ) -> Dict[float, Dict[int, BacktestResult]]:
        return {
            lat: self.run_all_hypotheses(hypotheses, raw_events, lat) for lat in latency_bands
        }

    def fills_to_dataframe(self, all_fills: List[FillRecord], raw: np.ndarray) -> pd.DataFrame:
        if not all_fills:
            return pd.DataFrame()
        rows = []
        ts_arr = raw["local_ts"]
        px_arr = raw["px"]
        for f in all_fills:
            row = {
                "timestamp_ns": f.timestamp_ns,
                "side": f.side,
                "exec_price": f.exec_price,
                "hypothesis_id": f.hypothesis_id,
                "signal": f.signal,
            }
            for h_ms in [100, 500, 1000, 5000]:
                target = f.timestamp_ns + h_ms * 1_000_000
                j = int(np.searchsorted(ts_arr, target, side="left"))
                j = min(j, len(px_arr) - 1)
                row[f"mid_price_plus_{h_ms}ms"] = float(px_arr[j])
            rows.append(row)
        return pd.DataFrame(rows)
