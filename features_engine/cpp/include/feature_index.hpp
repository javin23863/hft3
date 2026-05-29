#pragma once

#include <cstddef>

namespace hft {

// Must match features_engine/src/features/feature_index.py FeatureIndex
enum class FeatureIndex : size_t {
    AGGRESSOR_VOLUME_IMBALANCE = 0,
    BUY_AGGRESSOR_VOLUME = 1,
    SELL_AGGRESSOR_VOLUME = 2,
    CANCEL_TO_ADD_RATIO = 3,
    NEAR_TOUCH_CANCEL_PRESSURE = 4,
    TOP_1_DEPTH_BID = 5,
    TOP_1_DEPTH_ASK = 6,
    TOP_3_DEPTH_BID = 7,
    TOP_3_DEPTH_ASK = 8,
    TOP_5_DEPTH_BID = 9,
    TOP_5_DEPTH_ASK = 10,
    TOP_10_DEPTH_BID = 11,
    TOP_10_DEPTH_ASK = 12,
    BOOK_SLOPE = 13,
    BOOK_SLOPE_CHANGE = 14,
    SPREAD = 15,
    SPREAD_STRESS = 16,
    LIQUIDITY_VACUUM_SCORE = 17,
    QUEUE_DEPLETION_RATE_BID = 18,
    QUEUE_DEPLETION_RATE_ASK = 19,
    REFILL_RATIO = 20,
    ABSORPTION_SCORE = 21,
    ICEBERG_RELOAD_SCORE = 22,
    RELOAD_DROP_SCORE = 23,
    BID_ADD_CANCEL_RATIO = 24,
    ASK_ADD_CANCEL_RATIO = 25,
    REALIZED_VOL_STATE = 26,
    MID_PRICE = 40,
    FEATURE_DIM = 64
};

}  // namespace hft
