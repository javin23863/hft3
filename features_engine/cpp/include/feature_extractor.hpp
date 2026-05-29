#pragma once

#include <array>
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

namespace hft {

struct MBOEventCpp {
    int64_t timestamp_ns;
    int64_t order_id;
    char action;
    char side;
    double price;
    int32_t size;
};

struct BookLevelCpp {
    std::map<int64_t, int32_t> orders;
    int32_t total_qty = 0;
};

class FeatureExtractorCpp {
public:
    explicit FeatureExtractorCpp(double tick_size = 0.25);

    void reset();
    void process_event(const MBOEventCpp& event);
    const std::array<double, 64>& features() const { return vec_; }

private:
    void apply_book_event(const MBOEventCpp& event);
    void extract();

    double tick_size_;
    std::array<double, 64> vec_{};
    std::map<double, BookLevelCpp> bids_;
    std::map<double, BookLevelCpp> asks_;
    std::map<int64_t, std::pair<double, char>> order_map_;
    double best_bid_{0.0};
    double best_ask_{1e12};
    int64_t buy_agg_{0};
    int64_t sell_agg_{0};
    int64_t add_vol_{0};
    int64_t cancel_vol_{0};
    int64_t bid_add_{0};
    int64_t ask_add_{0};
    int64_t bid_cancel_{0};
    int64_t ask_cancel_{0};
    int64_t near_touch_cancel_{0};
    double prev_top10_depth_{0.0};
    double prev_book_slope_{0.0};
    int prev_bid1_{0};
    int prev_ask1_{0};
    std::vector<double> spread_history_;
};

}  // namespace hft
