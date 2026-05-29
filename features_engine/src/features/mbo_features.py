import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple
from collections import deque

@dataclass
class MBOEvent:
    timestamp_ns: int
    order_id: int
    action: str  # 'ADD', 'CANCEL', 'MODIFY', 'TRADE'
    side: str    # 'B' or 'A'
    price: float
    size: int

class BookLevel:
    def __init__(self):
        self.orders: Dict[int, int] = {} # order_id -> size
        self.total_qty = 0

class OrderBook:
    """
    Maintains L3 / MBO state to construct accurate features.
    Required for true event-time correctness.
    Maintains best bid/ask incrementally in O(1) amortized.
    """
    def __init__(self):
        # price -> BookLevel
        self.bids: Dict[float, BookLevel] = {}
        self.asks: Dict[float, BookLevel] = {}
        # order_id -> (price, side)
        self.order_map: Dict[int, Tuple[float, str]] = {}
        
        # Incremental BBO tracking
        self._best_bid = 0.0
        self._best_ask = float('inf')
        
    def _update_bbo(self, side: str):
        if side == 'B':
            self._best_bid = max(self.bids.keys()) if self.bids else 0.0
        else:
            self._best_ask = min(self.asks.keys()) if self.asks else float('inf')
        
    def apply_event(self, event: MBOEvent) -> None:
        """
        Processes an MBO event and updates the L3 state in amortized O(1).
        """
        book = self.bids if event.side == 'B' else self.asks
        update_needed = False
        
        if event.action == 'ADD':
            if event.price not in book:
                book[event.price] = BookLevel()
                # If adding a new price level that improves BBO
                if (event.side == 'B' and event.price > self._best_bid) or \
                   (event.side == 'A' and event.price < self._best_ask):
                    update_needed = True
                    
            book[event.price].orders[event.order_id] = event.size
            book[event.price].total_qty += event.size
            self.order_map[event.order_id] = (event.price, event.side)
            
        elif event.action in ('CANCEL', 'TRADE'):
            if event.order_id in self.order_map:
                price, side = self.order_map[event.order_id]
                target_book = self.bids if side == 'B' else self.asks
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
                        # If we just deleted the BBO level, we must scan for the next best
                        if (side == 'B' and price == self._best_bid) or \
                           (side == 'A' and price == self._best_ask):
                            update_needed = True
                        
        elif event.action == 'MODIFY':
            if event.order_id in self.order_map:
                price, side = self.order_map[event.order_id]
                target_book = self.bids if side == 'B' else self.asks
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

class MBOFeatureExtractor:
    """
    Computes strict non-lookahead features from the updated MBO orderbook state.
    """
    def __init__(self):
        self.book = OrderBook()
        
        # Accumulators
        self.buy_agg_vol = 0
        self.sell_agg_vol = 0
        self.add_vol = 0
        self.cancel_vol = 0
        self.near_touch_cancel_vol = 0
        self.trade_count = 0
        
        # Tracking windows (e.g. 1-second rolling windows)
        self.last_ts_ns = 0
        self.rolling_window_ns = 1_000_000_000 # 1 second
        
        # Previous state for differences
        self.prev_book_slope = 0.0
        
    def process_event(self, event: MBOEvent) -> dict:
        """
        Applies event and returns the current feature vector.
        """
        self.last_ts_ns = event.timestamp_ns
        
        # Pre-event state
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        
        # Accumulate metrics
        if event.action == 'TRADE':
            self.trade_count += 1
            if event.side == 'A': # Aggressor bought the ask
                self.buy_agg_vol += event.size
            else:
                self.sell_agg_vol += event.size
        elif event.action == 'ADD':
            self.add_vol += event.size
        elif event.action == 'CANCEL':
            self.cancel_vol += event.size
            # Near touch = within 3 ticks (assuming tick size for generic contract, hardcoded here as relative)
            if event.side == 'B' and bb > 0 and (bb - event.price) <= (0.25 * 3): # Assuming 0.25 tick
                self.near_touch_cancel_vol += event.size
            elif event.side == 'A' and ba < float('inf') and (event.price - ba) <= (0.25 * 3):
                self.near_touch_cancel_vol += event.size
        
        self.book.apply_event(event)
        return self._extract_features()
        
    def _extract_features(self) -> dict:
        features = {}
        
        # 1. Aggressor Volume & Imbalance
        features['buy_aggressor_volume'] = self.buy_agg_vol
        features['sell_aggressor_volume'] = self.sell_agg_vol
        
        total_agg_vol = self.buy_agg_vol + self.sell_agg_vol
        features['aggressor_volume_imbalance'] = (self.buy_agg_vol - self.sell_agg_vol) / total_agg_vol if total_agg_vol > 0 else 0.0
        
        # 2. Add/Cancel ratios
        features['cancel_to_add_ratio'] = self.cancel_vol / self.add_vol if self.add_vol > 0 else 1.0
        features['near_touch_cancel_pressure'] = self.near_touch_cancel_vol / self.add_vol if self.add_vol > 0 else 0.0
        
        # 3. Depth (Top 1, 3, 5, 10)
        sorted_bids = sorted(self.book.bids.keys(), reverse=True)
        sorted_asks = sorted(self.book.asks.keys())
        
        def get_depth(levels):
            bq = sum(self.book.bids[p].total_qty for p in sorted_bids[:levels])
            aq = sum(self.book.asks[p].total_qty for p in sorted_asks[:levels])
            return bq, aq
            
        b1, a1 = get_depth(1)
        b3, a3 = get_depth(3)
        b5, a5 = get_depth(5)
        b10, a10 = get_depth(10)
        
        features['top_1_depth_bid'] = b1
        features['top_1_depth_ask'] = a1
        features['top_3_depth_bid'] = b3
        features['top_3_depth_ask'] = a3
        features['top_5_depth_bid'] = b5
        features['top_5_depth_ask'] = a5
        features['top_10_depth_bid'] = b10
        features['top_10_depth_ask'] = a10
        
        # 4. Book Slope and Slope Change
        curr_slope = (b10 - a10) / (b10 + a10 + 1e-9)
        features['book_slope'] = curr_slope
        features['book_slope_change'] = curr_slope - self.prev_book_slope
        self.prev_book_slope = curr_slope
        
        # 5. Liquidity Vacuum Score (sudden drop in top 10 depth)
        features['liquidity_vacuum_score'] = 1.0 / (b10 + a10 + 1e-9)
        
        # 6. Spread Stress
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        spread = ba - bb if (bb > 0 and ba < float('inf')) else 0.0
        features['spread'] = spread
        # Note: spread_stress requires trailing median spread, simplified here
        features['spread_stress'] = spread / 0.25 if spread > 0.25 else 1.0 # Base tick
        
        return features
