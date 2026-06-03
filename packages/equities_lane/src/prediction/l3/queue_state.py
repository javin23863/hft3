from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .event_types import (
    AuctionImbalance,
    MBOAction,
    MBOEvent,
    MBOSide,
    OrderState,
    PriceLevel,
    SessionState,
)


@dataclass
class BookSnapshot:
    ts_ns: int
    best_bid: float
    best_ask: float
    bid_levels: list[PriceLevel]
    ask_levels: list[PriceLevel]
    midprice: float
    microprice: float
    spread: float
    total_bid_depth_1: int
    total_bid_depth_3: int
    total_bid_depth_5: int
    total_bid_depth_10: int
    total_ask_depth_1: int
    total_ask_depth_3: int
    total_ask_depth_5: int
    total_ask_depth_10: int
    order_book_imbalance: float


class OrderBookReconstructor:
    def __init__(self):
        self._orders: dict[int, OrderState] = {}
        self._bid_levels: dict[float, PriceLevel] = {}
        self._ask_levels: dict[float, PriceLevel] = {}
        self._best_bid: float = 0.0
        self._best_ask: float = 0.0
        self._last_ts_ns: int = 0
        self._event_count: int = 0
        self._auction_imbalances: list[AuctionImbalance] = []

    def process_event(self, event: MBOEvent) -> BookSnapshot | None:
        self._last_ts_ns = event.ts_event_ns
        self._event_count += 1

        if event.action == MBOAction.AUCTION:
            self._process_auction(event)
            return None

        if event.action == MBOAction.ADD:
            self._add_order(event)
        elif event.action == MBOAction.CANCEL or event.action == MBOAction.DELETE:
            self._cancel_order(event)
        elif event.action == MBOAction.MODIFY:
            self._modify_order(event)
        elif event.action == MBOAction.EXECUTE or event.action == MBOAction.TRADE:
            self._execute_order(event)
        elif event.action == MBOAction.FILL:
            self._fill_order(event)
        elif event.action == MBOAction.EXPIRED or event.action == MBOAction.SUSPEND:
            self._cancel_order(event)

        self._update_best_prices()
        return self.snapshot()

    def _add_order(self, event: MBOEvent):
        order = OrderState(
            order_id=event.order_id,
            side=event.side,
            price=event.price,
            size=event.size,
            remaining_size=event.remaining_size,
            add_ts_ns=event.ts_event_ns,
            last_modify_ts_ns=event.ts_event_ns,
            queue_position=event.queue_position,
            venue=event.venue,
        )
        self._orders[event.order_id] = order
        self._add_to_level(order)

    def _cancel_order(self, event: MBOEvent):
        if event.order_id in self._orders:
            order = self._orders[event.order_id]
            self._remove_from_level(order)
            del self._orders[event.order_id]
        else:
            levels = self._bid_levels if event.side == MBOSide.BID else self._ask_levels
            if event.price in levels:
                level = levels[event.price]
                level.total_size -= event.size
                if level.total_size <= 0:
                    del levels[event.price]

    def _modify_order(self, event: MBOEvent):
        if event.order_id in self._orders:
            order = self._orders[event.order_id]
            self._remove_from_level(order)
            order.price = event.price
            order.size = event.size
            order.remaining_size = event.remaining_size
            order.last_modify_ts_ns = event.ts_event_ns
            order.queue_position = event.queue_position
            self._add_to_level(order)

    def _execute_order(self, event: MBOEvent):
        if event.order_id in self._orders:
            order = self._orders[event.order_id]
            order.remaining_size -= event.size
            if order.remaining_size <= 0:
                self._remove_from_level(order)
                del self._orders[event.order_id]
            else:
                level = self._get_level(order)
                if level:
                    level.total_size -= event.size

    def _fill_order(self, event: MBOEvent):
        self._execute_order(event)

    def _process_auction(self, event: MBOEvent):
        imb = AuctionImbalance(
            ts_ns=event.ts_event_ns,
            paired_qty=0,
            total_imbalance_qty=event.size,
            imbalance_side=event.side,
            near_clearing_price=event.price,
            far_clearing_price=0.0,
            reference_price=0.0,
            session_state=event.session_state,
        )
        self._auction_imbalances.append(imb)

    def _add_to_level(self, order: OrderState):
        levels = self._bid_levels if order.side == MBOSide.BID else self._ask_levels
        if order.price not in levels:
            levels[order.price] = PriceLevel(
                price=order.price,
                total_size=0,
                order_count=0,
                orders=[],
            )
        level = levels[order.price]
        level.total_size += order.remaining_size
        level.order_count += 1
        level.orders.append(order)

    def _remove_from_level(self, order: OrderState):
        levels = self._bid_levels if order.side == MBOSide.BID else self._ask_levels
        if order.price in levels:
            level = levels[order.price]
            level.total_size -= order.remaining_size
            level.order_count -= 1
            if order in level.orders:
                level.orders.remove(order)
            if level.total_size <= 0 or level.order_count <= 0:
                del levels[order.price]

    def _get_level(self, order: OrderState) -> PriceLevel | None:
        levels = self._bid_levels if order.side == MBOSide.BID else self._ask_levels
        return levels.get(order.price)

    def _update_best_prices(self):
        if self._bid_levels:
            self._best_bid = max(self._bid_levels.keys())
        if self._ask_levels:
            self._best_ask = min(self._ask_levels.keys())

    def snapshot(self) -> BookSnapshot:
        bid_levels_sorted = sorted(
            self._bid_levels.values(), key=lambda x: x.price, reverse=True
        )
        ask_levels_sorted = sorted(
            self._ask_levels.values(), key=lambda x: x.price
        )

        midprice = (self._best_bid + self._best_ask) / 2.0 if self._best_bid > 0 and self._best_ask > 0 else 0.0
        spread = self._best_ask - self._best_bid if self._best_bid > 0 and self._best_ask > 0 else 0.0

        bid_size_1 = bid_levels_sorted[0].total_size if bid_levels_sorted else 0
        ask_size_1 = ask_levels_sorted[0].total_size if ask_levels_sorted else 0

        if bid_size_1 + ask_size_1 > 0:
            microprice = (self._best_ask * bid_size_1 + self._best_bid * ask_size_1) / (bid_size_1 + ask_size_1)
        else:
            microprice = midprice

        def depth_n(levels: list[PriceLevel], n: int) -> int:
            return sum(l.total_size for l in levels[:n])

        obi = 0.0
        if bid_size_1 + ask_size_1 > 0:
            obi = (bid_size_1 - ask_size_1) / (bid_size_1 + ask_size_1)

        return BookSnapshot(
            ts_ns=self._last_ts_ns,
            best_bid=self._best_bid,
            best_ask=self._best_ask,
            bid_levels=bid_levels_sorted,
            ask_levels=ask_levels_sorted,
            midprice=midprice,
            microprice=microprice,
            spread=spread,
            total_bid_depth_1=bid_size_1,
            total_bid_depth_3=depth_n(bid_levels_sorted, 3),
            total_bid_depth_5=depth_n(bid_levels_sorted, 5),
            total_bid_depth_10=depth_n(bid_levels_sorted, 10),
            total_ask_depth_1=ask_size_1,
            total_ask_depth_3=depth_n(ask_levels_sorted, 3),
            total_ask_depth_5=depth_n(ask_levels_sorted, 5),
            total_ask_depth_10=depth_n(ask_levels_sorted, 10),
            order_book_imbalance=obi,
        )

    def get_order(self, order_id: int) -> OrderState | None:
        return self._orders.get(order_id)

    def get_all_orders(self) -> list[OrderState]:
        return list(self._orders.values())

    def get_auction_imbalances(self) -> list[AuctionImbalance]:
        return self._auction_imbalances

    @property
    def event_count(self) -> int:
        return self._event_count
