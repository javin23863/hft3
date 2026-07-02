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
from replay.sensor_feature_adapter import PrecomputedFeatureAdapter
from replay.replay_clock import ReplayClock, deterministic_run_id
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from features_engine.src.hypotheses.modules import MarketState

_REPO = Path(__file__).resolve().parents[1]
AUDIT_DIR = _REPO / "runtime" / "replay_audits"


def _fifo_fill_state(
    fill_records: List[Dict[str, Any]],
    *,
    contract_multiplier: float = 1.0,
) -> tuple[float, list[list[float]], int]:
    """FIFO-match fills; return (realized_pnl, open_lots, open_side).

    open_lots rows are [price, qty, remaining_fee]; open_side is +1 long,
    -1 short, 0 flat. ``contract_multiplier`` scales the PRICE component of
    realized PnL into account currency (fees are already currency and are
    never scaled), matching hbt's linear_asset(contract_size) accounting.
    """
    open_side = 0
    open_lots: list[list[float]] = []  # [price, qty, remaining_fee]
    realized = 0.0
    for f in fill_records:
        side = str(f.get("side", "")).upper()
        px = float(f.get("exec_price", 0.0) or 0.0)
        qty = float(f.get("qty", 0.0) or 0.0)
        fee = float(f.get("fees", 0.0) or 0.0)
        if qty <= 0.0 or side not in ("BUY", "SELL"):
            continue
        sign = 1 if side == "BUY" else -1
        if open_side in (0, sign):
            open_lots.append([px, qty, fee])
            open_side = sign
            continue
        remaining = qty
        remaining_fee = fee
        while remaining > 1e-12 and open_lots:
            lot = open_lots[0]
            lot_qty_before = lot[1]
            remaining_before = remaining
            matched = min(remaining, lot_qty_before)
            open_fee = lot[2] * (matched / lot_qty_before) if lot_qty_before else 0.0
            close_fee = remaining_fee * (matched / remaining_before) if remaining_before else 0.0
            realized += (px - lot[0]) * matched * open_side * contract_multiplier - open_fee - close_fee
            lot[1] -= matched
            lot[2] -= open_fee
            remaining -= matched
            remaining_fee -= close_fee
            if lot[1] <= 1e-12:
                open_lots.pop(0)
        if remaining > 1e-12:
            open_lots.append([px, remaining, remaining_fee])
            open_side = sign
        elif not open_lots:
            open_side = 0
    return realized, open_lots, open_side


def _realized_closed_trade_pnl(fill_records: List[Dict[str, Any]]) -> float:
    realized, _open_lots, _open_side = _fifo_fill_state(fill_records)
    return realized


def _unrealized_pnl_marked_to_mid(
    fill_records: List[Dict[str, Any]],
    mark_mid: Optional[float],
    *,
    contract_multiplier: float = 1.0,
) -> Optional[float]:
    """Mark FIFO open inventory to the last observed mid. None when unmarkable.

    ``contract_multiplier`` converts price-point PnL into account currency so
    the mark matches the simulated account when ``linear_asset(contract_size)``
    is configured (e.g. MES = 5).
    """
    _realized, open_lots, open_side = _fifo_fill_state(fill_records)
    if not open_lots:
        return 0.0
    if mark_mid is None or mark_mid <= 0.0:
        return None
    return contract_multiplier * sum(
        (mark_mid - px) * qty * open_side for px, qty, _fee in open_lots
    )


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
    # True when one or both book sides are empty (liquidity vacuum). The
    # strategy is still stepped so it can cancel/flatten; it should suppress
    # new passive quotes itself while this is set.
    book_one_sided: bool = False


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
    # Feature feed latency in milliseconds.
    #
    # When set, the market-data adapter is synced to (current_timestamp -
    # feature_latency_ns) rather than current_timestamp, so the strategy
    # observes features that are as stale as the configured feed latency.
    # This mirrors the order-latency bands swept by hbt on the execution side,
    # ensuring that feature staleness and order latency are both modelled.
    #
    # Default (None): mirrors latency_ms — i.e. feature_latency_ms = latency_ms.
    # Set to 0.0 to give the strategy perfectly fresh features (no feed delay).
    # Values must be >= 0; negative values raise ValueError at runtime.
    #
    # Note: order latency is NOT affected; hbt already simulates that via its
    # own latency model.  Only the feature clock is shifted here.
    feature_latency_ms: Optional[float] = None
    # Cross-asset secondary symbols.  Maps symbol name → npz file path.
    # If non-empty, one HistoricalReplayMarketDataAdapter + MarketStatePipeline
    # is built per entry; at each step their features are injected into
    # ctx.market_state.cross_asset_features[symbol].
    cross_asset_npz: Dict[str, str] = field(default_factory=dict)
    # Pre-loaded event arrays for cross-asset symbols (symbol → np.ndarray).
    # Takes priority over cross_asset_npz when both are supplied for a symbol.
    cross_asset_events: Dict[str, np.ndarray] = field(default_factory=dict)
    # Precomputed sensor feature files injected into MarketState.cross_asset_features
    # by key. PIT clock = the file's own availability timestamps, observed with the
    # same feature-latency shift as primary features.
    sensor_feature_npz: Dict[str, str] = field(default_factory=dict)
    # Stepping mode.
    #
    # "event" (canonical): uses hbt.wait_next_feed(include_order_resp=True)
    #   throughout.  When no orders are live this jumps directly to the next
    #   market-data event (same as before).  When orders ARE live it wakes on
    #   fills/order responses OR the next feed event -- eliminating the 100us
    #   grid-stepping tax (3.3M elapse() calls per session) while preserving
    #   full hftbacktest queue/fill simulation fidelity.
    #   Decision timestamps shift to true event/order-resp times rather than
    #   100us-grid alignment (known re-baselining, same class as commit 49c3d80).
    # "grid": fixed step_ns elapse() stepping throughout (legacy; A/B only).
    # Override at runtime: HFT3_QUOTE_STEPPING=grid|event env var.
    step_mode: str = "event"
    # When True, npz_path points at content-addressed prepared replay data
    # (orphan filtering already applied).
    prepared_data: bool = False
    latency_model_path: str = ""
    fill_queue_model_path: str = ""
    # In-memory latency model dict (same shape as latency_model.json). Takes
    # priority over latency_model_path when both are provided — lets callers
    # like the HftBacktest-only lane pass separate entry/response latencies
    # without a temp file.
    latency_model: Optional[Dict[str, Any]] = None
    # Optional initial book snapshot NPZ passed through to
    # BacktestAsset.initial_snapshot (required for mid-session L3 captures).
    initial_snapshot: str = ""
    # Explicit run economics. When set they override the FeeModel product
    # default inside build_hftbacktest so executed fees match the caller's
    # declared config.
    maker_fee: Optional[float] = None
    taker_fee: Optional[float] = None
    contract_size: Optional[float] = None
    # Exchange fill model: NoPartialFillExchange (conservative base) or
    # PartialFillExchange (CME-realistic sensitivity axis).
    exchange_fill_model: str = "NoPartialFillExchange"


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
        self._fill_records: List[Dict[str, Any]] = []
        self._cum_filled_by_order: Dict[str, float] = {}
        self._max_abs_position = 0.0
        self._last_mid: Optional[float] = None

    def run(self) -> Dict[str, Any]:
        from backtest_pipeline.src.hft_backtest_builder import build_hftbacktest

        cfg = self.config

        # Resolve feature latency: default mirrors order latency_ms.
        feat_latency_ms = cfg.feature_latency_ms if cfg.feature_latency_ms is not None else cfg.latency_ms
        if feat_latency_ms < 0.0:
            raise ValueError(
                f"feature_latency_ms must be >= 0, got {feat_latency_ms}"
            )
        feat_latency_ns = int(feat_latency_ms * 1_000_000)

        if cfg.events is not None:
            fd, tmp_path = tempfile.mkstemp(suffix='.npz')
            os.close(fd)
            self._temp_npz = tmp_path
            np.savez_compressed(tmp_path, data=cfg.events)
            data_path = tmp_path
        else:
            data_path = cfg.npz_path

        try:
            latency_model = cfg.latency_model
            fill_queue_model = None
            if latency_model is None and cfg.latency_model_path:
                latency_model = json.loads(Path(cfg.latency_model_path).read_text(encoding="utf-8"))
            if cfg.fill_queue_model_path:
                fill_queue_model = json.loads(Path(cfg.fill_queue_model_path).read_text(encoding="utf-8"))
            queue_model_type = cfg.queue_model
            tick = cfg.tick_size
            lot = cfg.lot_size
            force_l3 = None
            if fill_queue_model:
                tick = float(fill_queue_model.get("tick_size") or tick)
                lot = float(fill_queue_model.get("lot_size") or lot)
                if fill_queue_model.get("fill_model_scope") == "l3_mbo":
                    force_l3 = True
                from backtest_pipeline.src.hftbacktest_realism import (
                    _official_replay_builder_queue_model_type,
                )

                queue_model_type = _official_replay_builder_queue_model_type(fill_queue_model)
            hbt = build_hftbacktest(
                data_path,
                latency_ms=cfg.latency_ms,
                latency_model=latency_model,
                queue_model_type=queue_model_type,
                tick_size=tick,
                lot_size=lot,
                product=cfg.product,
                prepared_data=cfg.prepared_data,
                force_l3=force_l3,
                initial_snapshot=cfg.initial_snapshot or None,
                maker_fee=cfg.maker_fee,
                taker_fee=cfg.taker_fee,
                contract_size=cfg.contract_size,
                exchange_fill_model=cfg.exchange_fill_model,
            )
            adapter = create_adapter(
                "REPLAY",
                hbt=hbt,
                run_id=self.run_id,
                latency_ms=cfg.latency_ms,
                queue_model=cfg.queue_model,
            )
            safety.assert_replay_safe(adapter, declared_mode="REPLAY")

            mda = HistoricalReplayMarketDataAdapter.from_npz(
                cfg.npz_path,
                events=cfg.events,
                tick_size=cfg.tick_size,
                latency_ms=cfg.latency_ms,
            )

            # Build one secondary adapter per cross-asset symbol.  Zero overhead
            # when cross_asset_npz is empty (the dict is never iterated).
            secondary_mdas: Dict[str, HistoricalReplayMarketDataAdapter] = {}
            for sym, npz_path in cfg.cross_asset_npz.items():
                sec_events = cfg.cross_asset_events.get(sym)
                secondary_mdas[sym] = HistoricalReplayMarketDataAdapter.from_npz(
                    npz_path,
                    events=sec_events,
                    tick_size=cfg.tick_size,
                    latency_ms=cfg.latency_ms,
                )
            # Build sensor feature adapters (precomputed npz, e.g. VIX options).
            # Zero overhead when sensor_feature_npz is empty.
            sensor_adapters: Dict[str, PrecomputedFeatureAdapter] = {
                k: PrecomputedFeatureAdapter(p)
                for k, p in cfg.sensor_feature_npz.items()
            }

            # Also wire any symbols provided only via cross_asset_events (no npz).
            for sym, sec_events in cfg.cross_asset_events.items():
                if sym not in secondary_mdas:
                    secondary_mdas[sym] = HistoricalReplayMarketDataAdapter.from_npz(
                        "",
                        events=sec_events,
                        tick_size=cfg.tick_size,
                        latency_ms=cfg.latency_ms,
                    )

            # Resolve stepping mode: env var HFT3_QUOTE_STEPPING overrides config
            # ("grid" forces legacy behaviour for A/B baselines; "event" is canonical).
            _stepping_env = os.environ.get("HFT3_QUOTE_STEPPING", "").lower()
            if _stepping_env in ("grid", "event"):
                effective_step_mode = _stepping_env
            else:
                effective_step_mode = cfg.step_mode

            event_stepping = effective_step_mode == "event"
            # Cap a single event-driven wait so the loop still advances the
            # clock through dead air in bounded jumps (1s of sim time).
            wait_cap_ns = max(cfg.step_ns, 1_000_000_000)

            steps = 0
            while True:
                if event_stepping:
                    # Event-driven: wake on fills/cancels OR the next feed event,
                    # whichever comes first.  With include_order_resp=True the
                    # simulator wakes us on order responses (fills, cancels) without
                    # 100us polling -- eliminating the grid-stepping tax while orders
                    # are open.  When no orders are live this is identical to the
                    # existing order-free fast path.
                    # Returns: 0=timeout  2=feed  3=order_resp  1=end  else=err
                    result = hbt.wait_next_feed(True, wait_cap_ns)
                    if result == 1:
                        break
                    if result not in (0, 2, 3):
                        return {"error": int(result), "steps": steps, "run_id": self.run_id}
                else:
                    result = hbt.elapse(cfg.step_ns)
                    if result == 1:
                        break
                    if result != 0:
                        return {"error": int(result), "steps": steps, "run_id": self.run_id}

                ts = int(hbt.current_timestamp)
                self.clock.advance_to(ts)
                # Adapter observes terminal order states and clears inactive
                # orders itself; clearing here first would hide fills from it.
                # Pass wake_reason so after_elapse can skip the orders() scan on
                # pure feed wakes (ret=2): hftbacktest guarantees that order state
                # cannot change without a ret=3 order-response wake.
                # Grid mode passes result=0 always (elapse never returns 2/3).
                adapter.after_elapse(ts, wake_reason=result)

                depth = hbt.depth(0)
                book_one_sided = depth.best_bid <= 0 or depth.best_ask <= 0
                if not book_one_sided:
                    self._last_mid = (float(depth.best_bid) + float(depth.best_ask)) / 2.0
                step_position = float(hbt.position(0))
                self._max_abs_position = max(self._max_abs_position, abs(step_position))

                # Sync the feature clock to ts - feat_latency_ns so the
                # strategy observes features that are as stale as the
                # configured feed latency.  Guard against negative timestamps
                # (possible at the very first step when ts < feat_latency_ns).
                feature_ts = max(0, ts - feat_latency_ns)
                mda.sync_to_timestamp(feature_ts)
                state = mda.current_market_state(cfg.symbol)

                # Inject cross-asset features when secondary adapters exist.
                if (secondary_mdas or sensor_adapters) and state is not None:
                    cross: Dict[str, Dict[str, float]] = {}
                    from replay.cross_asset_assembly import enrich_cross_leg
                    from replay.sensor_assembly import enrich_sensor_leg

                    for sym, sec_mda in secondary_mdas.items():
                        sec_mda.sync_to_timestamp(feature_ts)
                        sec_state = sec_mda.current_market_state(sym)
                        if sec_state is not None:
                            cross[sym] = enrich_cross_leg(
                                sec_state.primary_features,
                                symbol=sym,
                                source_timestamp_ns=feature_ts,
                            )
                    for k, ad in sensor_adapters.items():
                        ad.sync_to_timestamp(feature_ts)
                        feats = ad.current_features()
                        if feats is not None:
                            cross[k] = enrich_sensor_leg(
                                feats,
                                sensor_id=k,
                                source_timestamp_ns=feature_ts,
                                sensor_kind="vix_options" if k.upper() == "VIX" else "sensor",
                            )
                    if cross:
                        state = MarketState(
                            feature_vector=state.feature_vector,
                            primary_features=state.primary_features,
                            cross_asset_features=cross,
                            regime_posterior=state.regime_posterior,
                            regime_state=state.regime_state,
                            event_context=state.event_context,
                            volatility_state=state.volatility_state,
                            liquidity_state=state.liquidity_state,
                            latency_ms=state.latency_ms,
                            current_inventory=state.current_inventory,
                        )

                drained = adapter.drain_order_events()
                self._record_fills(drained, ts, depth)

                ctx = ReplayStepContext(
                    run_id=self.run_id,
                    clock=self.clock,
                    market_state=state,
                    best_bid=float(depth.best_bid),
                    best_ask=float(depth.best_ask),
                    position=step_position,
                    order_events=drained,
                    execution=adapter,
                    symbol=cfg.symbol,
                    book_one_sided=book_one_sided,
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
                "fill_events": list(self._fill_records),
                "order_lifecycle": list(self._lifecycle),
                "order_lifecycle_summary": summary,
                "lifecycle_path": str(cfg.audit_dir / f"{self.run_id}_order_lifecycle.jsonl"),
                "summary_path": str(cfg.audit_dir / f"{self.run_id}_summary.json"),
                "certification_stamp": stamp,
                "certification_footer": format_stamp_footer(stamp),
                "stepping_mode": effective_step_mode,
            }
        finally:
            if self._temp_npz is not None:
                try:
                    os.unlink(self._temp_npz)
                except OSError:
                    pass
                self._temp_npz = None

    def _record_fills(self, drained: List[OrderEvent], ts: int, depth: Any) -> None:
        """Record fill events into the lifecycle audit and the fill ledger.

        Submission/accept/cancel events are already recorded at action time;
        fills only surface asynchronously via the drain, so they are captured
        here together with the book state observed at drain time (used
        downstream for adverse-selection markouts).
        """
        for ev in drained:
            if ev.event_type not in (
                OrderEventType.ORDER_FILLED,
                OrderEventType.ORDER_PARTIALLY_FILLED,
            ):
                continue
            self._record_lifecycle(ev, ts, "", "")
            prev_cum = self._cum_filled_by_order.get(ev.order_id, 0.0)
            fill_qty = float(ev.filled_quantity) - prev_cum
            self._cum_filled_by_order[ev.order_id] = float(ev.filled_quantity)
            if fill_qty <= 0.0:
                continue
            best_bid = float(depth.best_bid)
            best_ask = float(depth.best_ask)
            self._fill_records.append(
                {
                    "timestamp_ns": int(ev.timestamp_ns),
                    "side": ev.side,
                    "exec_price": float(ev.avg_fill_price),
                    "qty": fill_qty,
                    "fees": float(ev.fees),
                    "best_bid": best_bid if best_bid > 0 else None,
                    "best_ask": best_ask if best_ask > 0 else None,
                }
            )

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
        # Report inventory economics in account currency: hbt's balance is
        # scaled by linear_asset(contract_size) when configured, so the FIFO
        # price component scales by the same multiplier (fees stay currency).
        contract_multiplier = (
            float(self.config.contract_size) if self.config.contract_size is not None else 1.0
        )
        realized_pnl, open_lots, _open_side = _fifo_fill_state(
            self._fill_records, contract_multiplier=contract_multiplier
        )
        unrealized_marked = _unrealized_pnl_marked_to_mid(
            self._fill_records, self._last_mid, contract_multiplier=contract_multiplier
        )
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
            "cash_balance": account.balance,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": account.unrealized_pnl,
            # Open inventory marked to the last observed two-sided mid.
            # None = inventory exists but no markable mid was seen.
            "unrealized_pnl_marked_to_mid": unrealized_marked,
            "open_inventory_lots": len(open_lots),
            "mark_mid": self._last_mid,
            "end_position": account.position,
            "max_inventory_excursion": self._max_abs_position,
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
