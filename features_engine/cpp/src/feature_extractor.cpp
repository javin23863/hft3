#include "feature_extractor.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <vector>

namespace hft {

FeatureExtractorCpp::FeatureExtractorCpp(double tick_size) : tick_size_(tick_size) {
    reset();
}

void FeatureExtractorCpp::reset() {
    vec_.fill(0.0);
    bids_.clear();
    asks_.clear();
    order_map_.clear();
    best_bid_ = 0.0;
    best_ask_ = 1e12;
    buy_agg_ = sell_agg_ = add_vol_ = cancel_vol_ = 0;
    bid_add_ = ask_add_ = bid_cancel_ = ask_cancel_ = 0;
    near_touch_cancel_ = 0;
    prev_top10_depth_ = 0.0;
    prev_book_slope_ = 0.0;
    prev_bid1_ = prev_ask1_ = 0;
    spread_history_.clear();
}

void FeatureExtractorCpp::apply_book_event(const MBOEventCpp& event) {
    auto& book = (event.side == 'B') ? bids_ : asks_;
    if (event.action == 'A') {
        if (book.find(event.price) == book.end()) {
            book[event.price] = BookLevelCpp{};
            if ((event.side == 'B' && event.price > best_bid_) ||
                (event.side == 'A' && event.price < best_ask_)) {
                if (event.side == 'B') best_bid_ = event.price;
                else best_ask_ = event.price;
            }
        }
        book[event.price].orders[event.order_id] = event.size;
        book[event.price].total_qty += event.size;
        order_map_[event.order_id] = {event.price, event.side};
        add_vol_ += event.size;
        if (event.side == 'B') bid_add_ += event.size;
        else ask_add_ += event.size;
    } else if (event.action == 'C' || event.action == 'T') {
        auto it = order_map_.find(event.order_id);
        if (it != order_map_.end()) {
            auto [price, side] = it->second;
            auto& tb = (side == 'B') ? bids_ : asks_;
            auto lit = tb.find(price);
            if (lit != tb.end()) {
                auto& lvl = lit->second;
                auto oit = lvl.orders.find(event.order_id);
                if (oit != lvl.orders.end()) {
                    int32_t rem = std::min(oit->second, event.size);
                    oit->second -= rem;
                    lvl.total_qty -= rem;
                    if (event.action == 'C') {
                        cancel_vol_ += rem;
                        if (side == 'B') bid_cancel_ += rem;
                        else ask_cancel_ += rem;
                        double near = tick_size_ * 3;
                        if (side == 'B' && best_bid_ > 0 && (best_bid_ - price) <= near)
                            near_touch_cancel_ += rem;
                        if (side == 'A' && best_ask_ < 1e11 && (price - best_ask_) <= near)
                            near_touch_cancel_ += rem;
                    }
                    if (oit->second <= 0) {
                        lvl.orders.erase(oit);
                        order_map_.erase(event.order_id);
                    }
                    if (lvl.total_qty <= 0) {
                        tb.erase(lit);
                        if ((side == 'B' && price == best_bid_) || (side == 'A' && price == best_ask_)) {
                            if (side == 'B')
                                best_bid_ = tb.empty() ? 0.0 : tb.rbegin()->first;
                            else
                                best_ask_ = tb.empty() ? 1e12 : tb.begin()->first;
                        }
                    }
                }
            }
        }
        if (event.action == 'T') {
            if (event.side == 'A') buy_agg_ += event.size;
            else sell_agg_ += event.size;
        }
    }
}

static int sum_top_k(const std::map<double, BookLevelCpp>& book, int k, bool bids) {
    std::vector<double> prices;
    prices.reserve(book.size());
    for (const auto& [p, _] : book) prices.push_back(p);
    if (bids)
        std::sort(prices.begin(), prices.end(), std::greater<double>());
    else
        std::sort(prices.begin(), prices.end());
    int total = 0;
    int n = std::min(k, static_cast<int>(prices.size()));
    for (int i = 0; i < n; ++i) total += book.at(prices[i]).total_qty;
    return total;
}

void FeatureExtractorCpp::process_event(const MBOEventCpp& event) {
    apply_book_event(event);
    extract();
}

void FeatureExtractorCpp::extract() {
    vec_.fill(0.0);
    const int64_t total_agg = buy_agg_ + sell_agg_;
    vec_[0] = total_agg > 0 ? static_cast<double>(buy_agg_ - sell_agg_) / total_agg : 0.0;
    vec_[1] = static_cast<double>(buy_agg_);
    vec_[2] = static_cast<double>(sell_agg_);
    vec_[3] = add_vol_ > 0 ? static_cast<double>(cancel_vol_) / add_vol_ : 1.0;
    vec_[4] = add_vol_ > 0 ? static_cast<double>(near_touch_cancel_) / add_vol_ : 0.0;
    vec_[24] = bid_cancel_ > 0 ? static_cast<double>(bid_add_) / bid_cancel_ : bid_add_;
    vec_[25] = ask_cancel_ > 0 ? static_cast<double>(ask_add_) / ask_cancel_ : ask_add_;

    const int b10 = sum_top_k(bids_, 10, true);
    const int a10 = sum_top_k(asks_, 10, false);
    const int b1 = sum_top_k(bids_, 1, true);
    const int a1 = sum_top_k(asks_, 1, false);
    vec_[5] = static_cast<double>(b1);
    vec_[6] = static_cast<double>(a1);
    vec_[11] = static_cast<double>(b10);
    vec_[12] = static_cast<double>(a10);

    const double slope = (b10 - a10) / (b10 + a10 + 1e-9);
    vec_[13] = slope;
    vec_[14] = slope - prev_book_slope_;
    prev_book_slope_ = slope;

    const double depth10 = b10 + a10;
    if (prev_top10_depth_ > 0)
        vec_[17] = std::max(0.0, (prev_top10_depth_ - depth10) / prev_top10_depth_);
    prev_top10_depth_ = depth10;

    if (best_bid_ > 0 && best_ask_ < 1e11) {
        const double spread = best_ask_ - best_bid_;
        vec_[15] = spread;
        spread_history_.push_back(spread);
        if (spread_history_.size() > 100) spread_history_.erase(spread_history_.begin());
        double median = spread;
        if (!spread_history_.empty()) {
            auto tmp = spread_history_;
            std::sort(tmp.begin(), tmp.end());
            median = tmp[tmp.size() / 2];
        }
        vec_[16] = median > 1e-9 ? spread / median : 1.0;
        vec_[40] = (best_bid_ + best_ask_) / 2.0;
    }

    const int bid_depl = std::max(0, prev_bid1_ - b1);
    const int ask_depl = std::max(0, prev_ask1_ - a1);
    vec_[18] = prev_bid1_ > 0 ? static_cast<double>(bid_depl) / prev_bid1_ : 0.0;
    vec_[19] = prev_ask1_ > 0 ? static_cast<double>(ask_depl) / prev_ask1_ : 0.0;
    prev_bid1_ = b1;
    prev_ask1_ = a1;
    vec_[20] = cancel_vol_ > 0 ? static_cast<double>(add_vol_) / cancel_vol_ : 1.0;
}

}  // namespace hft
