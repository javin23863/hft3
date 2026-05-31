"""Session backtester for low-float runner lane."""
from __future__ import annotations

from equities_lane.src.backtest.execution_sim import OrderIntent, simulate_fill
from equities_lane.src.backtest.signal_engine import entry_signal, exit_signal
from equities_lane.src.features.feature_registry import run_feature_pipeline
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.l3_policy import require_l3_session
from equities_lane.src.models import SessionTick
from equities_lane.src.patterns.consolidation import label_consolidation
from equities_lane.src.patterns.opening_range_breakout import label_opening_range_breakout
from equities_lane.src.types import BacktestResult, TradeFill, UniverseConfig


class LowFloatBacktester:
    def __init__(self, universe: UniverseConfig):
        self.universe = universe

    def run(
        self,
        session_path: str,
        *,
        ablation: str | None = None,
        allow_degraded: bool = False,
    ) -> BacktestResult:
        meta, ticks = load_session(session_path)
        require_l3_session(
            meta,
            l3_only=self.universe.l3_only,
            allow_degraded=allow_degraded,
            context="backtest",
        )
        features = run_feature_pipeline(ticks, self.universe, meta.degraded, ablation=ablation)

        fills: list[TradeFill] = []
        position = 0
        cash = 0.0
        entry_price = 0.0
        peak_equity = 0.0
        max_dd = 0.0
        equity_curve: list[float] = []
        failure_notes: list[str] = []

        for i, t in enumerate(ticks):
            prefix = ticks[: i + 1]
            orb = label_opening_range_breakout(prefix, meta, self.universe.patterns)
            consolidation = label_consolidation(prefix, self.universe.patterns)
            feat = features[i] if i < len(features) else features[-1]
            mid = _mid(t)

            if position == 0 and entry_signal(
                feat, orb, consolidation, self.universe.signals
            ):
                intent = OrderIntent(t.ts_ns, "buy", 100, t.ask_px)
                fill = simulate_fill(intent, t.bid_px, t.ask_px, self.universe.execution)
                if fill:
                    position = fill.qty
                    cash -= fill.price * fill.qty + fill.fees
                    entry_price = fill.price
                    fills.append(fill)

            elif position > 0 and exit_signal(feat, self.universe.signals):
                intent = OrderIntent(t.ts_ns, "sell", position, t.bid_px)
                fill = simulate_fill(intent, t.bid_px, t.ask_px, self.universe.execution)
                if fill:
                    cash += fill.price * fill.qty - fill.fees
                    position = 0
                    fills.append(fill)
                    if feat.hmm_state == "liquidation":
                        failure_notes.append(f"liquidation_exit@{t.ts_ns}")

            equity = cash + position * mid
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                max_dd = max(max_dd, (peak_equity - equity) / peak_equity)

        if position > 0 and ticks:
            t = ticks[-1]
            intent = OrderIntent(t.ts_ns, "sell", position, t.bid_px)
            fill = simulate_fill(intent, t.bid_px, t.ask_px, self.universe.execution)
            if fill:
                cash += fill.price * fill.qty - fill.fees
                fills.append(fill)
                position = 0

        net_pnl = cash
        turnover = sum(abs(f.price * f.qty) for f in fills)
        tail_loss = _tail_loss(equity_curve)

        return BacktestResult(
            symbol=meta.symbol,
            session_date=meta.session_date,
            net_pnl=net_pnl,
            num_trades=len(fills),
            max_drawdown=max_dd,
            turnover=turnover,
            tail_loss=tail_loss,
            fills=fills,
            degraded=meta.degraded,
            failure_notes=failure_notes,
        )


def _mid(t: SessionTick) -> float:
    if t.bid_px > 0 and t.ask_px > 0:
        return (t.bid_px + t.ask_px) / 2.0
    return t.trade_px or 0.0


def _tail_loss(curve: list[float]) -> float:
    if len(curve) < 2:
        return 0.0
    diffs = [curve[i] - curve[i - 1] for i in range(1, len(curve))]
    worst = min(diffs)
    return float(worst) if worst < 0 else 0.0
