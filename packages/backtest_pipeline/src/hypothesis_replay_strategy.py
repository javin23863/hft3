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
    """Single-hypothesis wrapper replacing SignalBacktester fill path."""

    def __init__(
        self,
        hypothesis: BaseHypothesis,
        *,
        signal_threshold: float = 0.15,
        order_qty: float = 1.0,
    ) -> None:
        self.hypothesis = hypothesis
        self.signal_threshold = signal_threshold
        self.order_qty = order_qty
        self._entered = False

    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent]:
        if ctx.market_state is None:
            return []
        if ctx.book_one_sided:
            return []
        sig = float(self.hypothesis.evaluate(ctx.market_state))
        pos = ctx.position
        intents: List[OrderIntent] = []
        model_id = f"HYP_{self.hypothesis.hyp_id}"

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
