import heapq
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
    """L3 book with O(1) amortized BBO; top-K via heapq (O(N log K), K=10)."""

    def __init__(self):
        self.bids: Dict[float, BookLevel] = {}
        self.asks: Dict[float, BookLevel] = {}
        self.order_map: Dict[int, Tuple[float, str]] = {}
        self._best_bid = 0.0
        self._best_ask = float("inf")

    def _update_bbo(self, side: str) -> None:
        if side == "B":
            self._best_bid = max(self.bids.keys()) if self.bids else 0.0
        else:
            self._best_ask = min(self.asks.keys()) if self.asks else float("inf")

    def apply_event(self, event: MBOEvent) -> None:
        book = self.bids if event.side == "B" else self.asks
        update_needed = False

        if event.action == "ADD":
            if event.price not in book:
                book[event.price] = BookLevel()
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
        bid_prices = heapq.nlargest(k, self.bids.keys()) if self.bids else []
        ask_prices = heapq.nsmallest(k, self.asks.keys()) if self.asks else []
        bq = sum(self.bids[p].total_qty for p in bid_prices)
        aq = sum(self.asks[p].total_qty for p in ask_prices)
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
        self._reload_at_level: Dict[Tuple[str, float], int] = {}
        self._prev_reload_score = 0.0
        self._trade_at_level: Dict[Tuple[str, float], int] = {}
        self._max_trade_size = 0
        self.last_ts_ns = 0
        self._prev_mid = 0.0
        self._mid_returns: Deque[float] = deque(maxlen=100)

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
            self._mid_returns.clear()
            self._prev_mid = 0.0
            self._max_trade_size = 0
        self.last_ts_ns = ts_ns

    def process_event(self, event: MBOEvent) -> np.ndarray:
        self._maybe_reset_window(event.timestamp_ns)
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        near_ticks = self.tick_size * 3

        if event.action == "TRADE":
            self._max_trade_size = max(self._max_trade_size, event.size)
            if event.side == "A":
                self.buy_agg_vol += event.size
            else:
                self.sell_agg_vol += event.size
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
        if self._max_trade_size > 0 and total_agg > 0:
            v[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] = (
                self._max_trade_size / total_agg
            ) * (1.0 if self.buy_agg_vol >= self.sell_agg_vol else -1.0)

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
        imb_l1 = (b1 - a1) / (b1 + a1 + 1e-9)
        imb_l10 = (b10 - a10) / (b10 + a10 + 1e-9)
        v[FeatureIndex.BOOK_SLOPE] = curr_slope
        v[FeatureIndex.BOOK_SLOPE_CHANGE] = curr_slope - self.prev_book_slope
        v[FeatureIndex.BOOK_IMBALANCE_L1] = imb_l1
        v[FeatureIndex.BOOK_IMBALANCE_L10] = imb_l10
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
        self._spread_history.append(spread)
        median_spread = (
            float(np.median(self._spread_history)) if self._spread_history else self.tick_size
        )
        v[FeatureIndex.SPREAD_STRESS] = (
            spread / median_spread if median_spread > 1e-9 else 1.0
        )

        if bb > 0 and ba < float("inf"):
            mid = (bb + ba) / 2.0
            v[FeatureIndex.MID_PRICE] = mid
            denom = float(b1 + a1)
            if denom > 0:
                v[FeatureIndex.MICROPRICE] = (bb * a1 + ba * b1) / denom
            if self._prev_mid > 0:
                self._mid_returns.append((mid - self._prev_mid) / self.tick_size)
            self._prev_mid = mid
            if len(self._mid_returns) >= 2:
                v[FeatureIndex.REALIZED_VOL_STATE] = float(np.std(self._mid_returns))
            elif len(self._mid_returns) == 1:
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
        v[FeatureIndex.ICEBERG_RELOAD_SCORE] = np.tanh(reload_score)
        v[FeatureIndex.RELOAD_DROP_SCORE] = max(
            0.0, self._prev_reload_score - abs(reload_score)
        )
        self._prev_reload_score = abs(reload_score)

        hit_vol = sum(self._trade_at_level.values())
        v[FeatureIndex.ABSORPTION_SCORE] = (
            hit_vol / (total_agg + 1e-9) if total_agg > 0 else 0.0
        ) * (1.0 - abs(curr_slope))

        return v
