"""Feature extraction — consolidated imports from features_engine.

Usage:
    from hft3.models.features import MBOEvent, MBOFeatureExtractor, OrderBook
"""

from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor, OrderBook
from features_engine.src.features.feature_index import (
    FeatureIndex,
    FEATURE_DIM,
    FEATURE_NAME_TO_INDEX,
    REGIME_INDEX_MAP,
    vector_to_feature_dict,
    new_feature_vector,
)
from features_engine.src.features.npz_feed import (
    load_npz_events,
    iter_mbo_events,
    events_list,
)
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
from features_engine.src.hypotheses.modules import MarketState, SpreadBlowoutRecompression

__all__ = [
    "MBOEvent",
    "MBOFeatureExtractor",
    "OrderBook",
    "FeatureIndex",
    "FEATURE_DIM",
    "FEATURE_NAME_TO_INDEX",
    "REGIME_INDEX_MAP",
    "vector_to_feature_dict",
    "new_feature_vector",
    "load_npz_events",
    "iter_mbo_events",
    "events_list",
    "MarketStatePipeline",
    "MarketState",
]
