import numpy as np
from dataclasses import dataclass
from typing import Dict, List

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
    """
    def __init__(self):
        # price -> BookLevel
        self.bids: Dict[float, BookLevel] = {}
        self.asks: Dict[float, BookLevel] = {}
        # order_id -> (price, side)
        self.order_map: Dict[int, tuple] = {}
        
    def apply_event(self, event: MBOEvent):
        """
        Processes an MBO event and updates the L3 state.
        """
        book = self.bids if event.side == 'B' else self.asks
        
        if event.action == 'ADD':
            if event.price not in book:
                book[event.price] = BookLevel()
            book[event.price].orders[event.order_id] = event.size
            book[event.price].total_qty += event.size
            self.order_map[event.order_id] = (event.price, event.side)
            
        elif event.action == 'CANCEL' or event.action == 'TRADE':
            if event.order_id in self.order_map:
                price, side = self.order_map[event.order_id]
                target_book = self.bids if side == 'B' else self.asks
                if price in target_book and event.order_id in target_book[price].orders:
                    # Partial or full cancel/trade
                    current_size = target_book[price].orders[event.order_id]
                    qty_to_remove = min(current_size, event.size)
                    
                    target_book[price].orders[event.order_id] -= qty_to_remove
                    target_book[price].total_qty -= qty_to_remove
                    
                    if target_book[price].orders[event.order_id] == 0:
                        del target_book[price].orders[event.order_id]
                        del self.order_map[event.order_id]
                        
                    if target_book[price].total_qty == 0:
                        del target_book[price]

class MBOFeatureExtractor:
    """
    Computes strict non-lookahead features from the updated MBO orderbook state.
    """
    def __init__(self):
        self.book = OrderBook()
        self.buy_agg_vol = 0
        self.sell_agg_vol = 0
        
    def process_event(self, event: MBOEvent) -> dict:
        """
        Applies event and returns the current feature vector.
        """
        self.book.apply_event(event)
        
        if event.action == 'TRADE':
            if event.side == 'A': # Aggressor bought the ask
                self.buy_agg_vol += event.size
            else:
                self.sell_agg_vol += event.size
                
        return self._extract_features()
        
    def _extract_features(self) -> dict:
        features = {}
        
        # 1. Aggressor Imbalance
        total_vol = self.buy_agg_vol + self.sell_agg_vol
        features['aggressor_imbalance'] = (self.buy_agg_vol - self.sell_agg_vol) / total_vol if total_vol > 0 else 0.0
        
        # 2. Book shape estimation (Top 5 levels)
        # Note: In C++ hot path, this is maintained incrementally in an array/tree, not sorted on demand
        sorted_bids = sorted(self.book.bids.keys(), reverse=True)
        sorted_asks = sorted(self.book.asks.keys())
        
        bid_qty = sum(self.book.bids[p].total_qty for p in sorted_bids[:5]) if sorted_bids else 0
        ask_qty = sum(self.book.asks[p].total_qty for p in sorted_asks[:5]) if sorted_asks else 0
        
        features['book_slope_top5'] = (bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-9)
        
        # Additional features would be calculated incrementally here...
        
        return features
