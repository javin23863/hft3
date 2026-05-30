"""
HftBacktest 2.x strategy: combined hypothesis signal -> orders with fees.
"""
from __future__ import annotations

from typing import List, Literal

import numpy as np
from hftbacktest.order import GTC, LIMIT

from features_engine.src.features.feature_index import FeatureIndex, vector_to_feature_dict
from features_engine.src.features.npz_feed import iter_mbo_events
from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
from features_engine.src.regime.regime_filter import RegimeFilter

AggregateMode = Literal["max_abs", "mean"]


class CombinedHypothesisStrategy:
    """Aggregates hypothesis signals; syncs MBO features when raw_events provided."""

    def __init__(
        self,
        hypotheses: List[BaseHypothesis],
        tick_size: float = 0.25,
        signal_threshold: float = 0.15,
        order_qty: float = 1.0,
        latency_ms: float = 1.0,
        raw_events: np.ndarray | None = None,
        aggregate_mode: AggregateMode = "max_abs",
    ):
        self.hypotheses = hypotheses
        self.tick_size = tick_size
        self.signal_threshold = signal_threshold
        self.order_qty = order_qty
        self.latency_ms = latency_ms
        self.aggregate_mode = aggregate_mode
        self._order_id = 1000
        self.regime_filter = RegimeFilter()
        self.pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=latency_ms)
        self._mbo_events = list(iter_mbo_events(raw_events)) if raw_events is not None else []
        self._mbo_idx = 0
        self._last_state: MarketState | None = None

    def _state_from_depth(self, hbt) -> MarketState:
        depth = hbt.depth(0)
        mid = (depth.best_bid + depth.best_ask) / 2.0
        spread = depth.best_ask - depth.best_bid
        vec = np.zeros(64)
        vec[FeatureIndex.MID_PRICE] = mid
        vec[FeatureIndex.SPREAD] = spread
        vec[FeatureIndex.SPREAD_STRESS] = spread / self.tick_size if spread > 0 else 1.0
        bid_qty = getattr(depth, "best_bid_qty", getattr(depth, "best_bid_tick", 0))
        ask_qty = getattr(depth, "best_ask_qty", getattr(depth, "best_ask_tick", 0))
        vec[FeatureIndex.TOP_1_DEPTH_BID] = bid_qty
        vec[FeatureIndex.TOP_1_DEPTH_ASK] = ask_qty
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
            current_inventory=int(hbt.position(0)),
            feature_vector=vec,
            regime_posterior=posterior,
        )

    def _advance_pipeline(self, hbt) -> MarketState | None:
        if not self._mbo_events:
            return None
        ts = int(hbt.current_timestamp)
        while self._mbo_idx < len(self._mbo_events):
            mbo = self._mbo_events[self._mbo_idx]
            if mbo.timestamp_ns > ts:
                break
            self._last_state = self.pipeline.process_event(mbo)
            self._mbo_idx += 1
        return self._last_state

    def _combined_signal(self, state: MarketState) -> float:
        signals = [h.evaluate(state) for h in self.hypotheses]
        if not signals:
            return 0.0
        if self.aggregate_mode == "mean":
            return sum(signals) / len(signals)
        return max(signals, key=lambda s: abs(s))

    def on_step(self, hbt) -> None:
        depth = hbt.depth(0)
        if depth.best_bid <= 0 or depth.best_ask <= 0:
            return

        state = self._advance_pipeline(hbt)
        if state is None:
            state = self._state_from_depth(hbt)
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
                current_inventory=int(hbt.position(0)),
            )

        combined = self._combined_signal(state)
        pos = hbt.position(0)

        if combined > self.signal_threshold and pos <= 0:
            self._submit(hbt, buy=True, price=depth.best_ask)
        elif combined < -self.signal_threshold and pos >= 0:
            self._submit(hbt, buy=False, price=depth.best_bid)

    def _submit(self, hbt, buy: bool, price: float) -> None:
        oid = self._order_id
        self._order_id += 1
        if buy:
            hbt.submit_buy_order(0, oid, price, self.order_qty, GTC, LIMIT, False)
        else:
            hbt.submit_sell_order(0, oid, price, self.order_qty, GTC, LIMIT, False)
