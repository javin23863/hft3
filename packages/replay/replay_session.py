"""Replay session orchestrator."""
from __future__ import annotations

import json
import tempfile
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np

from execution import safety
from execution.adapter_factory import create_adapter
from execution.interfaces import (
    CancelIntent,
    ExecutionAdapter,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    ReplaceIntent,
)
from replay.market_data_adapter import HistoricalReplayMarketDataAdapter
from replay.replay_clock import ReplayClock, deterministic_run_id
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer

_REPO = Path(__file__).resolve().parents[1]
AUDIT_DIR = _REPO / "runtime" / "replay_audits"


@dataclass
class ReplayStepContext:
    run_id: str
    clock: ReplayClock
    market_state: Any
    best_bid: float
    best_ask: float
    position: float
    order_events: List[OrderEvent]
    execution: ExecutionAdapter
    symbol: str


@runtime_checkable
class ReplayStrategy(Protocol):
    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent | CancelIntent | ReplaceIntent]: ...


@dataclass
class ReplaySessionConfig:
    npz_path: str = ""
    events: Optional[np.ndarray] = None
    run_id: str = ""
    latency_ms: float = 1.0
    queue_model: str = "LogProbQueueModel2"
    tick_size: float = 0.25
    lot_size: float = 1.0
    product: str = "MES"
    step_ns: int = 100_000
    max_steps: Optional[int] = None
    symbol: str = "MES"
    seed: int = 0
    audit_dir: Path = field(default_factory=lambda: AUDIT_DIR)


class ReplaySession:
    def __init__(
        self,
        config: ReplaySessionConfig,
        strategy: ReplayStrategy,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.run_id = config.run_id or deterministic_run_id(
            config.npz_path, config.latency_ms, config.queue_model, config.seed
        )
        self.clock = ReplayClock(seed=config.seed)
        self._lifecycle: List[dict[str, Any]] = []
        self._intent_count = 0
        self._temp_npz: Optional[str] = None

    def run(self) -> Dict[str, Any]:
        from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

        cfg = self.config
        if cfg.events is not None:
            fd, tmp_path = tempfile.mkstemp(suffix='.npz')
            os.close(fd)
            self._temp_npz = tmp_path
            np.savez_compressed(tmp_path, data=cfg.events)
            data_path = tmp_path
        else:
            data_path = cfg.npz_path

        try:
            hbt = build_hftbacktest(
                data_path,
                latency_ms=cfg.latency_ms,
                queue_model_type=cfg.queue_model,
                tick_size=cfg.tick_size,
                lot_size=cfg.lot_size,
                product=cfg.product,
            )
            adapter = create_adapter(
                "REPLAY",
                hbt=hbt,
                run_id=self.run_id,
                latency_ms=cfg.latency_ms,
                queue_model=cfg.queue_model,
            )
            safety.assert_replay_safe(adapter)

            mda = HistoricalReplayMarketDataAdapter.from_npz(
                cfg.npz_path,
                events=cfg.events,
                tick_size=cfg.tick_size,
                latency_ms=cfg.latency_ms,
            )

            steps = 0
            while True:
                result = hbt.elapse(cfg.step_ns)
                if result == 1:
                    break
                if result != 0:
                    return {"error": int(result), "steps": steps, "run_id": self.run_id}

                ts = int(hbt.current_timestamp)
                self.clock.advance_to(ts)
                hbt.clear_inactive_orders(0)
                adapter.after_elapse(ts)

                depth = hbt.depth(0)
                if depth.best_bid <= 0 or depth.best_ask <= 0:
                    steps += 1
                    if cfg.max_steps and steps >= cfg.max_steps:
                        break
                    continue

                mda.sync_to_timestamp(ts)
                state = mda.current_market_state(cfg.symbol)
                drained = adapter.drain_order_events()

                ctx = ReplayStepContext(
                    run_id=self.run_id,
                    clock=self.clock,
                    market_state=state,
                    best_bid=float(depth.best_bid),
                    best_ask=float(depth.best_ask),
                    position=float(hbt.position(0)),
                    order_events=drained,
                    execution=adapter,
                    symbol=cfg.symbol,
                )
                actions = self.strategy.on_step(ctx)
                for action in actions:
                    if isinstance(action, OrderIntent):
                        self._intent_count += 1
                        ev = adapter.submit_order(action)
                        self._record_lifecycle(ev, ts, action.strategy_id, action.model_id)
                    elif isinstance(action, CancelIntent):
                        ev = adapter.cancel_order(action.order_id)
                        self._record_lifecycle(ev, ts, action.strategy_id, action.model_id)
                    elif isinstance(action, ReplaceIntent):
                        ev = adapter.replace_order(action.order_id, action.new_order_intent)
                        self._record_lifecycle(ev, ts, action.new_order_intent.strategy_id, action.new_order_intent.model_id)

                steps += 1
                if cfg.max_steps and steps >= cfg.max_steps:
                    break

            account = adapter.get_account_state()
            summary = self._build_summary(adapter, account, steps)
            stamp = build_certification_stamp(
                execution_mode="REPLAY",
                execution_adapter_mode="hftbacktest_simulated_exchange",
                latency_band=self.config.latency_ms,
                queue_model=self.config.queue_model,
                fee_model="FeeModel",
                fill_model_version="hftbacktest",
            )
            summary["certification_stamp"] = stamp
            summary["certification_footer"] = format_stamp_footer(stamp)
            self._write_audits(summary)
            return {
                "run_id": self.run_id,
                "steps": steps,
                "balance": account.balance,
                "fee": account.fee,
                "num_trades": account.num_trades,
                "trading_volume": account.trading_volume,
                "position": account.position,
                "order_intent_count": self._intent_count,
                "order_lifecycle_summary": summary,
                "lifecycle_path": str(cfg.audit_dir / f"{self.run_id}_order_lifecycle.jsonl"),
                "summary_path": str(cfg.audit_dir / f"{self.run_id}_summary.json"),
                "certification_stamp": stamp,
                "certification_footer": format_stamp_footer(stamp),
            }
        finally:
            if self._temp_npz is not None:
                try:
                    os.unlink(self._temp_npz)
                except OSError:
                    pass
                self._temp_npz = None

    def _record_lifecycle(
        self,
        ev: OrderEvent,
        replay_time_ns: int,
        strategy_id: str,
        model_id: str,
    ) -> None:
        self._lifecycle.append(
            {
                "run_id": self.run_id,
                "timestamp_ns": ev.timestamp_ns,
                "replay_time_ns": replay_time_ns,
                "wall_time_ns": int(datetime.now(timezone.utc).timestamp() * 1e9),
                "event_type": ev.event_type.value,
                "intent_id": ev.intent_id,
                "order_id": ev.order_id,
                "symbol": ev.symbol,
                "side": ev.side,
                "price": ev.price,
                "quantity": ev.quantity,
                "filled_quantity": ev.filled_quantity,
                "remaining_quantity": ev.remaining_quantity,
                "avg_fill_price": ev.avg_fill_price,
                "fees": ev.fees,
                "latency_ms": ev.latency_ms,
                "queue_position_estimate": ev.queue_position_estimate,
                "source_adapter": ev.source_adapter,
                "strategy_id": strategy_id,
                "model_id": model_id,
                "event_context": "",
                "regime_state": "",
            }
        )

    def _build_summary(self, adapter: ExecutionAdapter, account: Any, steps: int) -> Dict[str, Any]:
        events = getattr(adapter, "all_events", [])
        counts = {t.value: 0 for t in OrderEventType}
        for ev in events:
            counts[ev.event_type.value] = counts.get(ev.event_type.value, 0) + 1

        counters = safety.counter_snapshot()
        return {
            "run_id": self.run_id,
            "order_intent_count": self._intent_count,
            "accepted_count": counts.get(OrderEventType.ORDER_ACCEPTED.value, 0),
            "rejected_count": counts.get(OrderEventType.ORDER_REJECTED.value, 0),
            "partial_fill_count": counts.get(OrderEventType.ORDER_PARTIALLY_FILLED.value, 0),
            "filled_count": counts.get(OrderEventType.ORDER_FILLED.value, 0),
            "cancel_count": counts.get(OrderEventType.ORDER_CANCELLED.value, 0),
            "replace_count": counts.get(OrderEventType.ORDER_REPLACED.value, 0),
            **counters,
            "total_fees": account.fee,
            "realized_pnl": account.balance,
            "unrealized_pnl": account.unrealized_pnl,
            "max_position": abs(account.position),
            "latency_band_ms": self.config.latency_ms,
            "queue_model": self.config.queue_model,
            "replay_start_time_ns": self.clock.start_ns,
            "replay_end_time_ns": self.clock.now_ns,
            "steps": steps,
        }

    def _write_audits(self, summary: Dict[str, Any]) -> None:
        cfg = self.config
        cfg.audit_dir.mkdir(parents=True, exist_ok=True)
        lc_path = cfg.audit_dir / f"{self.run_id}_order_lifecycle.jsonl"
        with lc_path.open("w", encoding="utf-8") as f:
            for row in self._lifecycle:
                f.write(json.dumps(row) + "\n")
        summary_path = cfg.audit_dir / f"{self.run_id}_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def run_replay_session(
    npz_path: str,
    strategy: ReplayStrategy,
    **kwargs: Any,
) -> Dict[str, Any]:
    cfg = ReplaySessionConfig(npz_path=npz_path, **kwargs)
    return ReplaySession(cfg, strategy).run()
