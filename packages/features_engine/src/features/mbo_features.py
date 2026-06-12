import bisect
import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple

import numpy as np

from .feature_index import (
    FEATURE_DIM,
    FeatureIndex,
    new_feature_vector,
)


@dataclass
class MBOEvent:
    timestamp_ns: int
    order_id: int
    action: str  # 'ADD', 'CANCEL', 'MODIFY', 'TRADE'
    side: str  # 'B' or 'A'
    price: float
    size: int


class BookLevel:
    def __init__(self):
        self.orders: Dict[int, int] = {}
        self.total_qty = 0


class OrderBook:
    """L3 book with O(1) amortized BBO; top-K via sorted price arrays + bisect."""

    def __init__(self):
        self.bids: Dict[float, BookLevel] = {}
        self.asks: Dict[float, BookLevel] = {}
        self.order_map: Dict[int, Tuple[float, str]] = {}
        self._best_bid = 0.0
        self._best_ask = float("inf")
        # Sorted price arrays: bids ascending (best bid = last), asks ascending (best ask = first)
        self._bid_prices: List[float] = []   # ascending; best bid = [-1]
        self._ask_prices: List[float] = []   # ascending; best ask = [0]

    def _update_bbo(self, side: str) -> None:
        if side == "B":
            self._best_bid = self._bid_prices[-1] if self._bid_prices else 0.0
        else:
            self._best_ask = self._ask_prices[0] if self._ask_prices else float("inf")

    def apply_event(self, event: MBOEvent) -> None:
        book = self.bids if event.side == "B" else self.asks
        sorted_prices = self._bid_prices if event.side == "B" else self._ask_prices
        update_needed = False

        if event.action == "ADD":
            if event.price not in book:
                book[event.price] = BookLevel()
                # Insert into sorted array at the correct position (bisect_left)
                idx = bisect.bisect_left(sorted_prices, event.price)
                sorted_prices.insert(idx, event.price)
                if (event.side == "B" and event.price > self._best_bid) or (
                    event.side == "A" and event.price < self._best_ask
                ):
                    update_needed = True
            book[event.price].orders[event.order_id] = event.size
            book[event.price].total_qty += event.size
            self.order_map[event.order_id] = (event.price, event.side)

        elif event.action in ("CANCEL", "TRADE"):
            if event.order_id in self.order_map:
                price, side = self.order_map[event.order_id]
                target_book = self.bids if side == "B" else self.asks
                target_sorted = self._bid_prices if side == "B" else self._ask_prices
                if price in target_book and event.order_id in target_book[price].orders:
                    current_size = target_book[price].orders[event.order_id]
                    qty_to_remove = min(current_size, event.size)
                    target_book[price].orders[event.order_id] -= qty_to_remove
                    target_book[price].total_qty -= qty_to_remove
                    if target_book[price].orders[event.order_id] == 0:
                        del target_book[price].orders[event.order_id]
                        del self.order_map[event.order_id]
                    if target_book[price].total_qty == 0:
                        del target_book[price]
                        # Remove from sorted array
                        idx = bisect.bisect_left(target_sorted, price)
                        if idx < len(target_sorted) and target_sorted[idx] == price:
                            target_sorted.pop(idx)
                        if (side == "B" and price == self._best_bid) or (
                            side == "A" and price == self._best_ask
                        ):
                            update_needed = True

        elif event.action == "MODIFY":
            if event.order_id in self.order_map:
                price, side = self.order_map[event.order_id]
                target_book = self.bids if side == "B" else self.asks
                if price in target_book and event.order_id in target_book[price].orders:
                    current_size = target_book[price].orders[event.order_id]
                    diff = event.size - current_size
                    target_book[price].orders[event.order_id] = event.size
                    target_book[price].total_qty += diff

        if update_needed:
            self._update_bbo(event.side)

    def get_best_bid(self) -> float:
        return self._best_bid

    def get_best_ask(self) -> float:
        return self._best_ask

    def top_k_depth(self, k: int) -> Tuple[int, int]:
        # Bids: best k = last k prices (descending from end of ascending list)
        bid_n = len(self._bid_prices)
        bq = 0
        for i in range(bid_n - 1, max(bid_n - 1 - k, -1), -1):
            bq += self.bids[self._bid_prices[i]].total_qty
        # Asks: best k = first k prices (ascending from start)
        ask_n = len(self._ask_prices)
        aq = 0
        for i in range(min(k, ask_n)):
            aq += self.asks[self._ask_prices[i]].total_qty
        return bq, aq


class MBOFeatureExtractor:
    """Indexed feature vector (64,) — no per-tick dict allocation on hot path."""

    def __init__(self, tick_size: float = 0.25, rolling_window_ns: int = 1_000_000_000):
        self.tick_size = tick_size
        self.rolling_window_ns = rolling_window_ns
        self.book = OrderBook()
        self._vec = new_feature_vector()

        self.buy_agg_vol = 0
        self.sell_agg_vol = 0
        self.add_vol = 0
        self.cancel_vol = 0
        self.bid_add_vol = 0
        self.ask_add_vol = 0
        self.bid_cancel_vol = 0
        self.ask_cancel_vol = 0
        self.near_touch_cancel_vol = 0

        self.prev_top10_depth = 0.0
        self.prev_book_slope = 0.0
        self.prev_bid1_qty = 0
        self.prev_ask1_qty = 0
        self._spread_history: Deque[float] = deque(maxlen=100)
        # Sorted list parallel to _spread_history for O(log N) exact sliding median
        self._spread_sorted: List[float] = []
        self._reload_at_level: Dict[Tuple[str, float], int] = {}
        self._prev_reload_score = 0.0
        self._trade_at_level: Dict[Tuple[str, float], int] = {}
        self.last_ts_ns = 0
        self._prev_mid = 0.0
        self._mid_returns: Deque[float] = deque(maxlen=100)
        # Running moments for windowed std (Welford-style sum/sumsq with eviction)
        self._ret_sum: float = 0.0
        self._ret_sumsq: float = 0.0

        # --- Accumulators for slots 31-34 ---
        # Slot 34 (MAX_CONTRACT_TRADE_IMBALANCE): size^2-weighted aggressor imbalance.
        # signed_size_sq = sum(sign * size^2); total_size_sq = sum(size^2)
        self._signed_size_sq: float = 0.0
        self._total_size_sq: float = 0.0

        # Slot 32 (PROP_REENTRY_SCORE): trade-rate acceleration x directional conviction.
        # We track trade count in the current window; prev_trade_count carries the
        # count from the previous window so we can compute acceleration at reset.
        self._curr_trade_count: int = 0
        self._prev_trade_count: int = 0

    def _maybe_reset_window(self, ts_ns: int) -> None:
        if self.last_ts_ns and (ts_ns - self.last_ts_ns) > self.rolling_window_ns:
            self.buy_agg_vol = 0
            self.sell_agg_vol = 0
            self.add_vol = 0
            self.cancel_vol = 0
            self.bid_add_vol = 0
            self.ask_add_vol = 0
            self.bid_cancel_vol = 0
            self.ask_cancel_vol = 0
            self.near_touch_cancel_vol = 0
            self._reload_at_level.clear()
            self._trade_at_level.clear()
            self._mid_returns.clear()
            self._ret_sum = 0.0
            self._ret_sumsq = 0.0
            self._prev_mid = 0.0
            # Slots 31-34: carry trade count into prev for acceleration signal,
            # then zero out all per-window state for the new window.
            self._prev_trade_count = self._curr_trade_count
            self._curr_trade_count = 0
            self._signed_size_sq = 0.0
            self._total_size_sq = 0.0
        self.last_ts_ns = ts_ns

    def process_event(self, event: MBOEvent) -> np.ndarray:
        self._maybe_reset_window(event.timestamp_ns)
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        near_ticks = self.tick_size * 3

        if event.action == "TRADE":
            if event.side == "A":
                self.buy_agg_vol += event.size
                # Slot 34: buy-aggressor → positive sign
                self._signed_size_sq += float(event.size) * float(event.size)
            else:
                self.sell_agg_vol += event.size
                # Slot 34: sell-aggressor → negative sign
                self._signed_size_sq -= float(event.size) * float(event.size)
            self._total_size_sq += float(event.size) * float(event.size)
            # Slot 32: count every trade in current window for rate-acceleration
            self._curr_trade_count += 1
            key = (event.side, event.price)
            self._trade_at_level[key] = self._trade_at_level.get(key, 0) + event.size
        elif event.action == "ADD":
            self.add_vol += event.size
            if event.side == "B":
                self.bid_add_vol += event.size
            else:
                self.ask_add_vol += event.size
            key = (event.side, event.price)
            self._reload_at_level[key] = self._reload_at_level.get(key, 0) + 1
        elif event.action == "CANCEL":
            self.cancel_vol += event.size
            if event.side == "B":
                self.bid_cancel_vol += event.size
            else:
                self.ask_cancel_vol += event.size
            if event.side == "B" and bb > 0 and (bb - event.price) <= near_ticks:
                self.near_touch_cancel_vol += event.size
            elif event.side == "A" and ba < float("inf") and (event.price - ba) <= near_ticks:
                self.near_touch_cancel_vol += event.size

        self.book.apply_event(event)
        return self._extract_features()

    def _extract_features(self) -> np.ndarray:
        v = self._vec
        v[:] = 0.0

        total_agg = self.buy_agg_vol + self.sell_agg_vol
        v[FeatureIndex.BUY_AGGRESSOR_VOLUME] = float(self.buy_agg_vol)
        v[FeatureIndex.SELL_AGGRESSOR_VOLUME] = float(self.sell_agg_vol)
        v[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE] = (
            (self.buy_agg_vol - self.sell_agg_vol) / total_agg if total_agg > 0 else 0.0
        )

        v[FeatureIndex.CANCEL_TO_ADD_RATIO] = (
            self.cancel_vol / self.add_vol if self.add_vol > 0 else 1.0
        )
        v[FeatureIndex.NEAR_TOUCH_CANCEL_PRESSURE] = (
            self.near_touch_cancel_vol / self.add_vol if self.add_vol > 0 else 0.0
        )
        v[FeatureIndex.BID_ADD_CANCEL_RATIO] = (
            self.bid_add_vol / (self.bid_cancel_vol + 1e-9)
        )
        v[FeatureIndex.ASK_ADD_CANCEL_RATIO] = (
            self.ask_add_vol / (self.ask_cancel_vol + 1e-9)
        )

        b1, a1 = self.book.top_k_depth(1)
        b3, a3 = self.book.top_k_depth(3)
        b5, a5 = self.book.top_k_depth(5)
        b10, a10 = self.book.top_k_depth(10)

        v[FeatureIndex.TOP_1_DEPTH_BID] = float(b1)
        v[FeatureIndex.TOP_1_DEPTH_ASK] = float(a1)
        v[FeatureIndex.TOP_3_DEPTH_BID] = float(b3)
        v[FeatureIndex.TOP_3_DEPTH_ASK] = float(a3)
        v[FeatureIndex.TOP_5_DEPTH_BID] = float(b5)
        v[FeatureIndex.TOP_5_DEPTH_ASK] = float(a5)
        v[FeatureIndex.TOP_10_DEPTH_BID] = float(b10)
        v[FeatureIndex.TOP_10_DEPTH_ASK] = float(a10)

        curr_slope = (b10 - a10) / (b10 + a10 + 1e-9)
        v[FeatureIndex.BOOK_SLOPE] = curr_slope
        v[FeatureIndex.BOOK_SLOPE_CHANGE] = curr_slope - self.prev_book_slope
        self.prev_book_slope = curr_slope

        depth10 = b10 + a10
        if self.prev_top10_depth > 0:
            drop = max(0.0, (self.prev_top10_depth - depth10) / self.prev_top10_depth)
            v[FeatureIndex.LIQUIDITY_VACUUM_SCORE] = drop
        self.prev_top10_depth = depth10

        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        spread = ba - bb if bb > 0 and ba < float("inf") else 0.0
        v[FeatureIndex.SPREAD] = spread
        # Sliding-window exact median via parallel sorted list
        if len(self._spread_history) == self._spread_history.maxlen:
            # Evict the oldest element before it gets dropped from the deque
            evicted = self._spread_history[0]
            idx = bisect.bisect_left(self._spread_sorted, evicted)
            if idx < len(self._spread_sorted) and self._spread_sorted[idx] == evicted:
                self._spread_sorted.pop(idx)
        bisect.insort(self._spread_sorted, spread)
        self._spread_history.append(spread)
        n = len(self._spread_sorted)
        if n == 0:
            median_spread = self.tick_size
        elif n % 2 == 1:
            median_spread = self._spread_sorted[n // 2]
        else:
            median_spread = (self._spread_sorted[n // 2 - 1] + self._spread_sorted[n // 2]) / 2.0
        v[FeatureIndex.SPREAD_STRESS] = (
            spread / median_spread if median_spread > 1e-9 else 1.0
        )

        if bb > 0 and ba < float("inf"):
            mid = (bb + ba) / 2.0
            v[FeatureIndex.MID_PRICE] = mid
            if self._prev_mid > 0:
                ret = (mid - self._prev_mid) / self.tick_size
                # Windowed running moments: evict oldest if deque is full, then append
                if len(self._mid_returns) == self._mid_returns.maxlen:
                    old_ret = self._mid_returns[0]
                    self._ret_sum -= old_ret
                    self._ret_sumsq -= old_ret * old_ret
                self._mid_returns.append(ret)
                self._ret_sum += ret
                self._ret_sumsq += ret * ret
            self._prev_mid = mid
            n_ret = len(self._mid_returns)
            if n_ret >= 2:
                mean = self._ret_sum / n_ret
                variance = self._ret_sumsq / n_ret - mean * mean
                v[FeatureIndex.REALIZED_VOL_STATE] = math.sqrt(max(0.0, variance))
            elif n_ret == 1:
                v[FeatureIndex.REALIZED_VOL_STATE] = abs(self._mid_returns[0])

        bid_depl = max(0, self.prev_bid1_qty - b1) / (self.prev_bid1_qty + 1e-9)
        ask_depl = max(0, self.prev_ask1_qty - a1) / (self.prev_ask1_qty + 1e-9)
        v[FeatureIndex.QUEUE_DEPLETION_RATE_BID] = bid_depl
        v[FeatureIndex.QUEUE_DEPLETION_RATE_ASK] = ask_depl
        self.prev_bid1_qty = b1
        self.prev_ask1_qty = a1

        v[FeatureIndex.REFILL_RATIO] = (
            self.add_vol / (self.cancel_vol + 1e-9) if self.cancel_vol > 0 else 1.0
        )

        reload_score = 0.0
        for key, reloads in self._reload_at_level.items():
            trades = self._trade_at_level.get(key, 0)
            if trades > 0 and reloads >= 2:
                side = 1.0 if key[0] == "B" else -1.0
                reload_score += side * min(1.0, reloads / (trades + 1e-9))
        v[FeatureIndex.ICEBERG_RELOAD_SCORE] = math.tanh(reload_score)
        v[FeatureIndex.RELOAD_DROP_SCORE] = max(
            0.0, self._prev_reload_score - abs(reload_score)
        )
        self._prev_reload_score = abs(reload_score)

        hit_vol = sum(self._trade_at_level.values())
        v[FeatureIndex.ABSORPTION_SCORE] = (
            hit_vol / (total_agg + 1e-9) if total_agg > 0 else 0.0
        ) * (1.0 - abs(curr_slope))

        # --- Slot 34: MAX_CONTRACT_TRADE_IMBALANCE ---
        # Size-squared-weighted aggressor imbalance: emphasises block prints.
        # sum(sign * size^2) / sum(size^2), range [-1, 1].  Distinct from slot 0
        # (which is volume-weighted) because squaring the size gives large trades
        # disproportionate weight — one block print dominates many small clips.
        # Zero when no trades have occurred in this window.
        v[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] = (
            self._signed_size_sq / self._total_size_sq
            if self._total_size_sq > 0.0
            else 0.0
        )

        # --- Slot 32: PROP_REENTRY_SCORE ---
        # Trade-rate acceleration × directional conviction.
        # Captures a re-entry burst: flow surging back in with a clear side.
        # trade_rate_accel = (curr_count - prev_count) / (prev_count + 1)
        # reentry = tanh(accel) * aggressor_imbalance
        # Zero when trade counts are flat or there is no directional conviction.
        trade_rate_accel = (self._curr_trade_count - self._prev_trade_count) / (
            self._prev_trade_count + 1
        )
        aggressor_imbalance = v[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE]
        v[FeatureIndex.PROP_REENTRY_SCORE] = math.tanh(trade_rate_accel) * aggressor_imbalance

        # --- Slot 31: CUTOFF_PRESSURE_SCORE ---
        # One-sided forced-exit pressure = directional persistence × intensity.
        # one_sidedness = |buy - sell| / total  (always in [0, 1])
        # intensity     = tanh(total_agg / window_norm)  (bounded squeeze)
        # score         = sign(dominant_side) * one_sidedness * intensity
        # High when flow is both one-sided AND heavy; low when balanced or thin.
        # Distinct from slot 0: multiplies one-sidedness by absolute magnitude,
        # so moderate imbalance at high volume reads differently than at thin volume.
        # Zero when no aggressor flow.
        _WINDOW_NORM = 100.0
        if total_agg > 0:
            one_sidedness = abs(self.buy_agg_vol - self.sell_agg_vol) / total_agg
            intensity = math.tanh(total_agg / _WINDOW_NORM)
            dominant_sign = 1.0 if self.buy_agg_vol >= self.sell_agg_vol else -1.0
            v[FeatureIndex.CUTOFF_PRESSURE_SCORE] = dominant_sign * one_sidedness * intensity
        else:
            v[FeatureIndex.CUTOFF_PRESSURE_SCORE] = 0.0

        # --- Slot 33: NEWS_RESTRICTION_FLATTEN_SCORE ---
        # Forced-flat unwind signature: one-sided aggressor exits coupled with
        # passive-quote pulls at the near touch.
        # cancel_pressure = tanh(near_touch_cancel_pressure)  (reuses slot 4 input)
        # score = sign(buy - sell) * (|buy - sell| / total) * cancel_pressure
        # Non-zero only when flow is one-sided AND near-touch cancels are present,
        # capturing simultaneous market exits + passive pull (news-restriction flatten).
        # Zero when no aggressor flow or no near-touch cancels.
        if total_agg > 0 and self.near_touch_cancel_vol > 0:
            raw_cancel_pressure = (
                self.near_touch_cancel_vol / self.add_vol if self.add_vol > 0 else 1.0
            )
            cancel_pressure = math.tanh(raw_cancel_pressure)
            side_sign = 1.0 if self.buy_agg_vol >= self.sell_agg_vol else -1.0
            one_sided_frac = abs(self.buy_agg_vol - self.sell_agg_vol) / total_agg
            v[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] = (
                side_sign * one_sided_frac * cancel_pressure
            )
        else:
            v[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] = 0.0

        return v
