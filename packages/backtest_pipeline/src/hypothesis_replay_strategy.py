"""Mode-blind replay strategy wrappers."""
from __future__ import annotations

from typing import List, Literal

import numpy as np

from execution.interfaces import OrderIntent, new_intent_id
from features_engine.src.features.feature_index import FeatureIndex, vector_to_feature_dict
from features_engine.src.features.npz_feed import iter_mbo_events
from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
from features_engine.src.regime.regime_filter import RegimeFilter
from replay.replay_session import ReplayStepContext, ReplayStrategy

AggregateMode = Literal["max_abs", "mean"]


class ToyAlwaysLongStrategy:
    """Deterministic toy strategy: emits one BUY when book is valid and flat."""

    def __init__(self, *, model_id: str = "TOY") -> None:
        self._emitted = False
        self._model_id = model_id

    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent]:
        if self._emitted or ctx.position > 0:
            return []
        if ctx.best_bid <= 0 or ctx.best_ask <= 0:
            return []
        self._emitted = True
        return [
            OrderIntent(
                intent_id=new_intent_id(),
                run_id=ctx.run_id,
                timestamp_ns=ctx.clock.now_ns,
                strategy_id="toy_always_long",
                model_id=self._model_id,
                symbol=ctx.symbol,
                side="BUY",
                order_type="LIMIT",
                price=ctx.best_ask,
                quantity=1.0,
                latency_budget_ms=1.0,
                reason_code="toy_entry",
            )
        ]


class HypothesisReplayStrategy:
    """Single-hypothesis wrapper for ReplaySession-backed backtesting.

    Exit semantics mirror the VectorBT screening lane so parameter surfaces
    mean the same thing in both engines: ``stop_loss_pct``/``take_profit_pct``
    are percentages of entry price, ``holding_period_bars`` counts bars of
    ``bar_duration_ns`` (default 1 minute, matching the screen's freq="1min").
    All exits are optional; with none configured the behavior is unchanged
    (entries + signal-flip only).
    """

    #: Re-emit an unfilled exit intent at most once per this many nanoseconds.
    EXIT_REEMIT_NS = 1_000_000_000

    def __init__(
        self,
        hypothesis: BaseHypothesis,
        *,
        signal_threshold: float = 0.15,
        order_qty: float = 1.0,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        holding_period_bars: int | None = None,
        bar_duration_ns: int = 60_000_000_000,
        max_holding_ns: int | None = None,
    ) -> None:
        self.hypothesis = hypothesis
        self.signal_threshold = signal_threshold
        self.order_qty = order_qty
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        if max_holding_ns is not None:
            self.max_holding_ns: int | None = int(max_holding_ns)
        elif holding_period_bars is not None:
            self.max_holding_ns = int(holding_period_bars) * int(bar_duration_ns)
        else:
            self.max_holding_ns = None
        self._entered = False
        self._entry_price: float | None = None
        self._entry_ts: int | None = None
        self._last_exit_emit_ns: int | None = None

    def _track_entry(self, ctx: ReplayStepContext) -> None:
        """Maintain entry price/timestamp from observed fills and position."""
        if ctx.position == 0:
            self._entry_price = None
            self._entry_ts = None
            self._last_exit_emit_ns = None
            return
        if self._entry_price is not None:
            return
        fill_price = None
        for ev in ctx.order_events:
            if ev.event_type.value in ("ORDER_FILLED", "ORDER_PARTIALLY_FILLED"):
                fill_price = float(ev.avg_fill_price)
        if fill_price is None or fill_price <= 0.0:
            if ctx.best_bid <= 0 or ctx.best_ask <= 0:
                # One-sided book and no fill price observed: defer entry
                # anchoring until a fill event or a two-sided book appears.
                return
            fill_price = (ctx.best_bid + ctx.best_ask) / 2.0
        self._entry_price = fill_price
        self._entry_ts = ctx.clock.now_ns

    def _exit_reason(self, ctx: ReplayStepContext) -> str:
        """Return exit reason_code, or empty string when no exit triggers.

        Stop/take-profit need a mid (both book sides); the holding-expiry
        exit is time-based and fires even on a one-sided book, so a position
        can still flatten while one side is in a liquidity vacuum.
        """
        if ctx.position == 0 or self._entry_price is None or self._entry_price <= 0.0:
            return ""
        if ctx.best_bid > 0 and ctx.best_ask > 0:
            mid = (ctx.best_bid + ctx.best_ask) / 2.0
            direction = 1.0 if ctx.position > 0 else -1.0
            pnl_frac = direction * (mid - self._entry_price) / self._entry_price
            if self.stop_loss_pct is not None and pnl_frac <= -self.stop_loss_pct / 100.0:
                return "stop_loss"
            if self.take_profit_pct is not None and pnl_frac >= self.take_profit_pct / 100.0:
                return "take_profit"
        if (
            self.max_holding_ns is not None
            and self._entry_ts is not None
            and ctx.clock.now_ns - self._entry_ts >= self.max_holding_ns
        ):
            return "max_holding"
        return ""

    def _exit_intent(self, ctx: ReplayStepContext, reason: str, model_id: str) -> OrderIntent | None:
        # Marketable exit at the touch: sell into the bid / buy from the ask.
        # Returns None when the side needed to close has no price (that side
        # of the book is empty) — retry next step.
        exiting_long = ctx.position > 0
        price = ctx.best_bid if exiting_long else ctx.best_ask
        if price <= 0:
            return None
        return OrderIntent(
            intent_id=new_intent_id(),
            run_id=ctx.run_id,
            timestamp_ns=ctx.clock.now_ns,
            strategy_id="hypothesis_replay",
            model_id=model_id,
            symbol=ctx.symbol,
            side="SELL" if exiting_long else "BUY",
            order_type="LIMIT",
            price=price,
            quantity=abs(ctx.position),
            reduce_only=True,
            latency_budget_ms=1.0,
            reason_code=reason,
        )

    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent]:
        # Exit paths run BEFORE the one-sided/no-state suppression: the whole
        # point of stepping strategies on one-sided books is that they can
        # still flatten. A short position only needs the ask side to close.
        self._track_entry(ctx)
        model_id = f"HYP_{self.hypothesis.hyp_id}"

        exit_reason = self._exit_reason(ctx)
        if exit_reason:
            intent = self._exit_intent(ctx, exit_reason, model_id)
            if intent is None:
                return []
            now = ctx.clock.now_ns
            if (
                self._last_exit_emit_ns is not None
                and now - self._last_exit_emit_ns < self.EXIT_REEMIT_NS
            ):
                return []
            self._last_exit_emit_ns = now
            return [intent]

        # New entries need market state and a two-sided book.
        if ctx.market_state is None:
            return []
        if ctx.book_one_sided:
            return []

        sig = float(self.hypothesis.evaluate(ctx.market_state))
        pos = ctx.position
        intents: List[OrderIntent] = []

        if sig > self.signal_threshold and pos <= 0:
            intents.append(
                OrderIntent(
                    intent_id=new_intent_id(),
                    run_id=ctx.run_id,
                    timestamp_ns=ctx.clock.now_ns,
                    strategy_id="hypothesis_replay",
                    model_id=model_id,
                    symbol=ctx.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    price=ctx.best_ask,
                    quantity=self.order_qty,
                    latency_budget_ms=1.0,
                    reason_code="signal_long",
                )
            )
            self._entered = True
        elif sig < -self.signal_threshold and pos >= 0:
            intents.append(
                OrderIntent(
                    intent_id=new_intent_id(),
                    run_id=ctx.run_id,
                    timestamp_ns=ctx.clock.now_ns,
                    strategy_id="hypothesis_replay",
                    model_id=model_id,
                    symbol=ctx.symbol,
                    side="SELL",
                    order_type="LIMIT",
                    price=ctx.best_bid,
                    quantity=self.order_qty,
                    latency_budget_ms=1.0,
                    reason_code="signal_short",
                )
            )
            self._entered = True
        return intents


class CombinedHypothesisReplayStrategy:
    """Combined hypothesis aggregation via adapter-backed OrderIntent emission."""

    def __init__(
        self,
        hypotheses: List[BaseHypothesis],
        raw_events: np.ndarray,
        *,
        tick_size: float = 0.25,
        signal_threshold: float = 0.15,
        order_qty: float = 1.0,
        latency_ms: float = 1.0,
        aggregate_mode: AggregateMode = "max_abs",
    ) -> None:
        self.hypotheses = hypotheses
        self.tick_size = tick_size
        self.signal_threshold = signal_threshold
        self.order_qty = order_qty
        self.latency_ms = latency_ms
        self.aggregate_mode = aggregate_mode
        self.regime_filter = RegimeFilter()
        self.pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=latency_ms)
        self._mbo_events = list(iter_mbo_events(raw_events))
        self._mbo_idx = 0
        self._last_state: MarketState | None = None

    def _combined_signal(self, state: MarketState) -> float:
        signals = [h.evaluate(state) for h in self.hypotheses]
        if not signals:
            return 0.0
        if self.aggregate_mode == "mean":
            return sum(signals) / len(signals)
        return max(signals, key=lambda s: abs(s))

    def _state_from_depth(self, ctx: ReplayStepContext) -> MarketState:
        mid = (ctx.best_bid + ctx.best_ask) / 2.0
        spread = ctx.best_ask - ctx.best_bid
        vec = np.zeros(64)
        vec[FeatureIndex.MID_PRICE] = mid
        vec[FeatureIndex.SPREAD] = spread
        vec[FeatureIndex.SPREAD_STRESS] = spread / self.tick_size if spread > 0 else 1.0
        feat = vector_to_feature_dict(vec)
        posterior = self.regime_filter.update(feat, "NORMAL")
        return MarketState(
            primary_features=feat,
            cross_asset_features={},
            regime_state=RegimeFilter.argmax(posterior),
            event_context="NORMAL",
            volatility_state=self.regime_filter.volatility_state(feat),
            liquidity_state=self.regime_filter.liquidity_state(feat),
            latency_ms=self.latency_ms,
            current_inventory=int(ctx.position),
            feature_vector=vec,
            regime_posterior=posterior,
        )

    def _advance_pipeline(self, ts: int) -> MarketState | None:
        while self._mbo_idx < len(self._mbo_events):
            mbo = self._mbo_events[self._mbo_idx]
            if mbo.timestamp_ns > ts:
                break
            self._last_state = self.pipeline.process_event(mbo)
            self._mbo_idx += 1
        return self._last_state

    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent]:
        if ctx.book_one_sided:
            return []
        state = self._advance_pipeline(ctx.clock.now_ns)
        if state is None:
            state = ctx.market_state or self._state_from_depth(ctx)
        else:
            state = MarketState(
                feature_vector=state.feature_vector,
                primary_features=state.primary_features,
                cross_asset_features=state.cross_asset_features,
                regime_posterior=state.regime_posterior,
                regime_state=state.regime_state,
                event_context=state.event_context,
                volatility_state=state.volatility_state,
                liquidity_state=state.liquidity_state,
                latency_ms=self.latency_ms,
                current_inventory=int(ctx.position),
            )

        combined = self._combined_signal(state)
        pos = ctx.position
        intents: List[OrderIntent] = []

        if combined > self.signal_threshold and pos <= 0:
            intents.append(
                OrderIntent(
                    intent_id=new_intent_id(),
                    run_id=ctx.run_id,
                    timestamp_ns=ctx.clock.now_ns,
                    strategy_id="combined_hypothesis",
                    model_id="COMBINED_HYP",
                    symbol=ctx.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    price=ctx.best_ask,
                    quantity=self.order_qty,
                    event_context=state.event_context,
                    regime_state=state.regime_state,
                    reason_code="combined_long",
                )
            )
        elif combined < -self.signal_threshold and pos >= 0:
            intents.append(
                OrderIntent(
                    intent_id=new_intent_id(),
                    run_id=ctx.run_id,
                    timestamp_ns=ctx.clock.now_ns,
                    strategy_id="combined_hypothesis",
                    model_id="COMBINED_HYP",
                    symbol=ctx.symbol,
                    side="SELL",
                    order_type="LIMIT",
                    price=ctx.best_bid,
                    quantity=self.order_qty,
                    event_context=state.event_context,
                    regime_state=state.regime_state,
                    reason_code="combined_short",
                )
            )
        return intents
