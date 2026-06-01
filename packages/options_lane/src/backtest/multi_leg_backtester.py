"""Multi-leg parity arb backtest with latency deferral."""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path

from options_lane.src.ingest.quote_aligner import align_quotes, snapshot_has_required_legs
from options_lane.src.models import LegQuote, ParityGroup, QuoteSnapshot
from options_lane.src.parity_engine import compute_violation, is_actionable, theoretical_spread
from options_lane.src.backtest.options_fee_model import OptionsFeeModel


@dataclass
class ArbFill:
    timestamp_ns: int
    group_id: str
    side: str
    role: str
    price: float
    residual: float


@dataclass
class ParityBacktestResult:
    group_id: str
    net_pnl: float
    num_arbs: int
    max_violation_ticks: float
    fills: list[ArbFill] = field(default_factory=list)


@dataclass
class _PendingArb:
    exec_time_ns: int
    violation_residual: float


class MultiLegParityBacktester:
    def __init__(
        self,
        fee_model: OptionsFeeModel | None = None,
        latency_ms: float = 1.0,
    ):
        self.fee_model = fee_model or OptionsFeeModel()
        self.latency_ms = latency_ms

    def run(
        self,
        group: ParityGroup,
        quotes: list[LegQuote],
    ) -> ParityBacktestResult:
        snapshots = align_quotes(group, quotes)
        latency_ns = int(self.latency_ms * 1_000_000)
        pending: list[_PendingArb] = []
        fills: list[ArbFill] = []
        net_pnl = 0.0
        max_violation_ticks = 0.0
        num_arbs = 0
        fee_per_leg = self.fee_model.fee_per_contract(group)

        for snap in snapshots:
            if not snapshot_has_required_legs(group, snap):
                continue

            still_pending: list[_PendingArb] = []
            for action in pending:
                if action.exec_time_ns <= snap.timestamp_ns:
                    exec_snap = self._find_exec_snapshot(snapshots, action.exec_time_ns)
                    if exec_snap is None:
                        continue
                    pnl_delta, leg_fills = self._execute_arb(
                        group, exec_snap, action.violation_residual, action.exec_time_ns
                    )
                    net_pnl += pnl_delta
                    fills.extend(leg_fills)
                    num_arbs += 1
                else:
                    still_pending.append(action)
            pending = still_pending

            violation = compute_violation(
                group, snap, fee_per_leg=fee_per_leg, as_of_ns=snap.timestamp_ns
            )
            if violation is None:
                continue
            max_violation_ticks = max(max_violation_ticks, violation.edge_ticks)
            if is_actionable(violation, group):
                same_dir = any(
                    (p.violation_residual > 0) == (violation.residual > 0)
                    for p in pending
                )
                if not same_dir:
                    pending.append(
                        _PendingArb(
                            exec_time_ns=snap.timestamp_ns + latency_ns,
                            violation_residual=violation.residual,
                        )
                    )

        for action in pending:
            exec_snap = self._find_exec_snapshot(snapshots, action.exec_time_ns)
            if exec_snap is None:
                continue
            pnl_delta, leg_fills = self._execute_arb(
                group, exec_snap, action.violation_residual, action.exec_time_ns
            )
            net_pnl += pnl_delta
            fills.extend(leg_fills)
            num_arbs += 1

        return ParityBacktestResult(
            group_id=group.id,
            net_pnl=net_pnl,
            num_arbs=num_arbs,
            max_violation_ticks=max_violation_ticks,
            fills=fills,
        )

    def _exec_snapshot(self, snap: QuoteSnapshot, exec_ts: int) -> QuoteSnapshot:
        filtered = {
            role: q for role, q in snap.quotes.items() if q.timestamp_ns <= exec_ts
        }
        return QuoteSnapshot(
            group_id=snap.group_id, timestamp_ns=exec_ts, quotes=filtered
        )

    def _find_exec_snapshot(
        self, snapshots: list[QuoteSnapshot], exec_ts: int
    ) -> QuoteSnapshot | None:
        times = [s.timestamp_ns for s in snapshots]
        idx = bisect.bisect_right(times, exec_ts) - 1
        if idx < 0:
            return None
        return self._exec_snapshot(snapshots[idx], exec_ts)

    def _count_legs(self, group: ParityGroup, exec_quotes: dict[str, LegQuote]) -> int:
        if group.type == "index_future_basis":
            n = sum(1 for role in ("call", "put", "spot") if role in exec_quotes)
            if "future" in exec_quotes:
                n += 1
            return n
        n = sum(1 for role in ("call", "put") if role in exec_quotes)
        if group.type == "futures_options" and "future" in exec_quotes:
            n += 1
        return n

    def _execute_arb(
        self,
        group: ParityGroup,
        exec_snap: QuoteSnapshot,
        residual: float,
        exec_ts: int,
    ) -> tuple[float, list[ArbFill]]:
        exec_quotes = {
            role: q
            for role, q in exec_snap.quotes.items()
            if q.timestamp_ns <= exec_ts
        }
        call = exec_quotes.get("call")
        put = exec_quotes.get("put")
        if call is None or put is None:
            return 0.0, []
        try:
            theo = theoretical_spread(group, exec_quotes)
        except ValueError:
            return 0.0, []
        num_legs = self._count_legs(group, exec_quotes)
        fees = self.fee_model.total_leg_cost(group, num_legs)

        direction = "sell_rich" if residual > 0 else "buy_rich"
        leg_fills: list[ArbFill] = []

        if residual > 0:
            call_px = call.bid
            put_px = put.ask
            gross = call_px - put_px - theo
            leg_fills = [
                ArbFill(exec_ts, group.id, direction, "call", call_px, residual),
                ArbFill(exec_ts, group.id, direction, "put", put_px, residual),
            ]
            if group.type == "futures_options" and "future" in exec_quotes:
                fut = exec_quotes["future"]
                gross -= fut.ask - fut.mid
                leg_fills.append(
                    ArbFill(exec_ts, group.id, "hedge", "future", fut.ask, residual)
                )
        else:
            call_px = call.ask
            put_px = put.bid
            gross = theo - (call_px - put_px)
            leg_fills = [
                ArbFill(exec_ts, group.id, direction, "call", call_px, residual),
                ArbFill(exec_ts, group.id, direction, "put", put_px, residual),
            ]
            if group.type == "futures_options" and "future" in exec_quotes:
                fut = exec_quotes["future"]
                gross -= fut.mid - fut.bid
                leg_fills.append(
                    ArbFill(exec_ts, group.id, "hedge", "future", fut.bid, residual)
                )

        pnl = gross - fees
        return pnl, leg_fills


def write_research_card(result: ParityBacktestResult, output_dir: str | Path) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.group_id}.json"
    payload = {
        "group_id": result.group_id,
        "net_pnl": result.net_pnl,
        "num_arbs": result.num_arbs,
        "max_violation_ticks": result.max_violation_ticks,
        "fills": [
            {
                "timestamp_ns": f.timestamp_ns,
                "side": f.side,
                "role": f.role,
                "price": f.price,
                "residual": f.residual,
            }
            for f in result.fills
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
