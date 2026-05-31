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

    def __post_init__(self) -> None:
        self.extractor.tick_size = self.tick_size
        if self.event_engine is None:
            self.event_engine = EventContextEngine()

    def process_event(self, event: MBOEvent) -> MarketState:
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
            round_dist = abs((mid / self.tick_size) % 4.0 - 2.0) / 4.0
            vec[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] = round_dist
            vec[FeatureIndex.DISTANCE_TO_VWAP] = abs(feat_dict.get("spread", 0.0)) / self.tick_size
            spread_stress = feat_dict.get("spread_stress", 1.0)
            vec[FeatureIndex.IS_BREAKING_LEVEL] = 1.0 if spread_stress > 2.0 else 0.0
            vec[FeatureIndex.IS_BREAKING_SESSION_LEVEL] = vec[FeatureIndex.IS_BREAKING_LEVEL]

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
