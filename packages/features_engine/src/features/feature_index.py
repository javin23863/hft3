"""
Fixed feature index aligned with C++ std::array<double, 64>.
"""
from __future__ import annotations

import enum
from typing import Dict

import numpy as np

FEATURE_DIM = 64


class FeatureIndex(enum.IntEnum):
    AGGRESSOR_VOLUME_IMBALANCE = 0
    BUY_AGGRESSOR_VOLUME = 1
    SELL_AGGRESSOR_VOLUME = 2
    CANCEL_TO_ADD_RATIO = 3
    NEAR_TOUCH_CANCEL_PRESSURE = 4
    TOP_1_DEPTH_BID = 5
    TOP_1_DEPTH_ASK = 6
    TOP_3_DEPTH_BID = 7
    TOP_3_DEPTH_ASK = 8
    TOP_5_DEPTH_BID = 9
    TOP_5_DEPTH_ASK = 10
    TOP_10_DEPTH_BID = 11
    TOP_10_DEPTH_ASK = 12
    BOOK_SLOPE = 13
    BOOK_SLOPE_CHANGE = 14
    SPREAD = 15
    SPREAD_STRESS = 16
    LIQUIDITY_VACUUM_SCORE = 17
    QUEUE_DEPLETION_RATE_BID = 18
    QUEUE_DEPLETION_RATE_ASK = 19
    REFILL_RATIO = 20
    ABSORPTION_SCORE = 21
    ICEBERG_RELOAD_SCORE = 22
    RELOAD_DROP_SCORE = 23
    BID_ADD_CANCEL_RATIO = 24
    ASK_ADD_CANCEL_RATIO = 25
    REALIZED_VOL_STATE = 26
    SPREAD_STRESS_ELEVATED = 27  # renamed from IS_BREAKING_LEVEL: spread_stress > 2.0 proxy
    IS_BREAKING_SESSION_LEVEL = 28
    DISTANCE_TO_ROUND_NUMBER = 29
    DISTANCE_TO_VWAP = 30
    CUTOFF_PRESSURE_SCORE = 31
    PROP_REENTRY_SCORE = 32
    NEWS_RESTRICTION_FLATTEN_SCORE = 33
    MAX_CONTRACT_TRADE_IMBALANCE = 34
    MID_PRICE = 40
    REGIME_NORMAL = 41
    REGIME_EVENT_SHOCK = 42
    REGIME_LIQUIDITY_VACUUM = 43
    REGIME_STOP_CASCADE = 44
    REGIME_PROP_FLATTEN = 45
    REGIME_BOOK_REBUILD = 46
    REGIME_CHOP = 47
    REGIME_TREND = 48
    REGIME_SPREAD_STRESS = 49


FEATURE_NAME_TO_INDEX: Dict[str, int] = {
    "aggressor_volume_imbalance": FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE,
    "buy_aggressor_volume": FeatureIndex.BUY_AGGRESSOR_VOLUME,
    "sell_aggressor_volume": FeatureIndex.SELL_AGGRESSOR_VOLUME,
    "cancel_to_add_ratio": FeatureIndex.CANCEL_TO_ADD_RATIO,
    "near_touch_cancel_pressure": FeatureIndex.NEAR_TOUCH_CANCEL_PRESSURE,
    "top_1_depth_bid": FeatureIndex.TOP_1_DEPTH_BID,
    "top_1_depth_ask": FeatureIndex.TOP_1_DEPTH_ASK,
    "top_3_depth_bid": FeatureIndex.TOP_3_DEPTH_BID,
    "top_3_depth_ask": FeatureIndex.TOP_3_DEPTH_ASK,
    "top_5_depth_bid": FeatureIndex.TOP_5_DEPTH_BID,
    "top_5_depth_ask": FeatureIndex.TOP_5_DEPTH_ASK,
    "top_10_depth_bid": FeatureIndex.TOP_10_DEPTH_BID,
    "top_10_depth_ask": FeatureIndex.TOP_10_DEPTH_ASK,
    "book_slope": FeatureIndex.BOOK_SLOPE,
    "book_slope_change": FeatureIndex.BOOK_SLOPE_CHANGE,
    "spread": FeatureIndex.SPREAD,
    "spread_stress": FeatureIndex.SPREAD_STRESS,
    "liquidity_vacuum_score": FeatureIndex.LIQUIDITY_VACUUM_SCORE,
    "queue_depletion_rate_bid": FeatureIndex.QUEUE_DEPLETION_RATE_BID,
    "queue_depletion_rate_ask": FeatureIndex.QUEUE_DEPLETION_RATE_ASK,
    "refill_ratio": FeatureIndex.REFILL_RATIO,
    "absorption_score": FeatureIndex.ABSORPTION_SCORE,
    "iceberg_reload_score": FeatureIndex.ICEBERG_RELOAD_SCORE,
    "reload_drop_score": FeatureIndex.RELOAD_DROP_SCORE,
    "bid_add_cancel_ratio": FeatureIndex.BID_ADD_CANCEL_RATIO,
    "ask_add_cancel_ratio": FeatureIndex.ASK_ADD_CANCEL_RATIO,
    "spread_stress_elevated": FeatureIndex.SPREAD_STRESS_ELEVATED,
    "is_breaking_session_level": FeatureIndex.IS_BREAKING_SESSION_LEVEL,
    "distance_to_round_number": FeatureIndex.DISTANCE_TO_ROUND_NUMBER,
    "distance_to_vwap": FeatureIndex.DISTANCE_TO_VWAP,
    "cutoff_pressure_score": FeatureIndex.CUTOFF_PRESSURE_SCORE,
    "prop_reentry_score": FeatureIndex.PROP_REENTRY_SCORE,
    "news_restriction_flatten_score": FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE,
    "max_contract_trade_imbalance": FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE,
    "mid_price": FeatureIndex.MID_PRICE,
}

REGIME_INDEX_MAP = {
    "normal": FeatureIndex.REGIME_NORMAL,
    "event_shock": FeatureIndex.REGIME_EVENT_SHOCK,
    "liquidity_vacuum": FeatureIndex.REGIME_LIQUIDITY_VACUUM,
    "stop_cascade": FeatureIndex.REGIME_STOP_CASCADE,
    "prop_flatten": FeatureIndex.REGIME_PROP_FLATTEN,
    "book_rebuild": FeatureIndex.REGIME_BOOK_REBUILD,
    "chop": FeatureIndex.REGIME_CHOP,
    "trend_continuation": FeatureIndex.REGIME_TREND,
    "spread_stress": FeatureIndex.REGIME_SPREAD_STRESS,
}


def vector_to_feature_dict(vec: np.ndarray) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for name, idx in FEATURE_NAME_TO_INDEX.items():
        if idx < len(vec):
            out[name] = float(vec[idx])
    return out


def new_feature_vector() -> np.ndarray:
    return np.zeros(FEATURE_DIM, dtype=np.float64)
