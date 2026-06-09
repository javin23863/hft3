"""
Builds full MarketState X_t from MBO events: features, regime posterior, event context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from features_engine.src.features.feature_index import (
    FEATURE_DIM,
    FeatureIndex,
    REGIME_INDEX_MAP,
    vector_to_feature_dict,
)
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.regime.event_context import EventContextEngine
from features_engine.src.regime.regime_filter import RegimeFilter


@dataclass
class MarketStatePipeline:
    tick_size: float = 0.25
    extractor: MBOFeatureExtractor = field(default_factory=MBOFeatureExtractor)
    regime_filter: RegimeFilter = field(default_factory=RegimeFilter)
    event_engine: Optional[EventContextEngine] = None
    latency_ms: float = 1.0
    current_inventory: int = 0
    cross_asset_features: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Round-number increment in price units.  ES/MES use 10-point levels
    # (100-point levels are also significant but 10 captures the most common
    # stopping/sweep targets).  Set to None to disable (emits 0).
    round_number_increment: Optional[float] = 10.0

    # --- running session state (not dataclass-serialised) ---
    # VWAP: accumulated via trade events only.  If the MBO event stream does
    # not distinguish trades from quotes, DISTANCE_TO_VWAP will be 0 until
    # the first TRADE event arrives.  Proxy note: "TRADE" action on MBOEvent
    # is used as the trade filter; all non-TRADE events are excluded from the
    # VWAP accumulator.
    _vwap_sum_px_qty: float = field(default=0.0, init=False, repr=False)
    _vwap_sum_qty: float = field(default=0.0, init=False, repr=False)

    # Session high/low tracked from mid-price across all processed events.
    _session_high: float = field(default=0.0, init=False, repr=False)
    _session_low: float = field(default=float("inf"), init=False, repr=False)

    def __post_init__(self) -> None:
        self.extractor.tick_size = self.tick_size
        if self.event_engine is None:
            self.event_engine = EventContextEngine()

    def reset_session(self) -> None:
        """Reset intra-session accumulators (call at session boundary if known)."""
        self._vwap_sum_px_qty = 0.0
        self._vwap_sum_qty = 0.0
        self._session_high = 0.0
        self._session_low = float("inf")

    def _update_vwap(self, event: MBOEvent) -> None:
        """Accumulate trade-based VWAP: sum(price * size) / sum(size) over TRADE events."""
        if event.action == "TRADE" and event.size > 0:
            self._vwap_sum_px_qty += event.price * event.size
            self._vwap_sum_qty += event.size

    def _current_vwap(self) -> Optional[float]:
        """Return running VWAP or None if no trades have been seen yet."""
        if self._vwap_sum_qty > 0.0:
            return self._vwap_sum_px_qty / self._vwap_sum_qty
        return None

    def process_event(self, event: MBOEvent) -> MarketState:
        # Update VWAP accumulator before the extractor applies the event so
        # that the trade price/size are still raw (the book doesn't matter here).
        self._update_vwap(event)

        vec = self.extractor.process_event(event)
        feat_dict = vector_to_feature_dict(vec)

        assert self.event_engine is not None
        event_ctx = self.event_engine.resolve_ns(event.timestamp_ns)
        posterior = self.regime_filter.update(feat_dict, event_ctx)

        for regime, prob in posterior.items():
            idx = REGIME_INDEX_MAP.get(regime)
            if idx is not None:
                vec[idx] = prob

        regime_argmax = RegimeFilter.argmax(posterior)
        vol_state = self.regime_filter.volatility_state(feat_dict)
        liq_state = self.regime_filter.liquidity_state(feat_dict)

        mid = feat_dict.get("mid_price", 0.0)
        if mid > 0:
            # --- DISTANCE_TO_VWAP ---
            # Signed distance in ticks: positive when mid is above VWAP
            # (instrument trading through VWAP from below), negative when below.
            # Emits 0.0 until the first TRADE event arrives.
            vwap = self._current_vwap()
            if vwap is not None:
                vec[FeatureIndex.DISTANCE_TO_VWAP] = (mid - vwap) / self.tick_size
            else:
                vec[FeatureIndex.DISTANCE_TO_VWAP] = 0.0

            # --- SPREAD_STRESS_ELEVATED (index 27, renamed from IS_BREAKING_LEVEL) ---
            # Binary flag: 1 when spread_stress > 2.0 (i.e. spread is more than
            # twice its rolling median).  This is a spread-regime proxy, not a
            # price-level breakout.  Renamed to reflect what it actually measures.
            spread_stress = feat_dict.get("spread_stress", 1.0)
            vec[FeatureIndex.SPREAD_STRESS_ELEVATED] = 1.0 if spread_stress > 2.0 else 0.0

            # --- IS_BREAKING_SESSION_LEVEL ---
            # Update session high/low and set flag when the current mid takes
            # out the prior session extreme.  Signed: +1 for high break, -1 for
            # low break, 0 otherwise.  Session resets only via reset_session().
            prev_high = self._session_high
            prev_low = self._session_low
            if self._session_high == 0.0 and self._session_low == float("inf"):
                # First event of the session; initialise without triggering a break.
                self._session_high = mid
                self._session_low = mid
                vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = 0.0
            elif mid > prev_high:
                self._session_high = mid
                vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = 1.0
            elif mid < prev_low:
                self._session_low = mid
                vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = -1.0
            else:
                vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = 0.0

            # --- DISTANCE_TO_ROUND_NUMBER ---
            # Distance from mid to the nearest multiple of round_number_increment,
            # normalised to ticks.  For ES/MES the default increment is 10 points
            # (40 ticks at 0.25/tick).  Set round_number_increment=None to disable.
            if self.round_number_increment is not None and self.round_number_increment > 0:
                inc = self.round_number_increment
                remainder = mid % inc
                dist_pts = min(remainder, inc - remainder)  # distance to nearest multiple
                vec[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] = dist_pts / self.tick_size
            else:
                vec[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] = 0.0

        return MarketState(
            feature_vector=vec,
            primary_features=feat_dict,
            cross_asset_features=self.cross_asset_features,
            regime_posterior=posterior,
            regime_state=regime_argmax,
            event_context=event_ctx,
            volatility_state=vol_state,
            liquidity_state=liq_state,
            latency_ms=self.latency_ms,
            current_inventory=self.current_inventory,
        )
