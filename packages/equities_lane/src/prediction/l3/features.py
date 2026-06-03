from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .event_types import MBOAction, MBOEvent, MBOSide, SessionState
from .queue_state import BookSnapshot, OrderBookReconstructor


@dataclass
class L3Features:
    ask_depletion_ratio: float = 0.0
    bid_depletion_ratio: float = 0.0
    net_depletion_ratio: float = 0.0
    top_ask_consumption: float = 0.0
    multi_level_ask_consumption: float = 0.0
    shares_removed_ask_per_ns: float = 0.0
    price_levels_cleared_per_sec: float = 0.0
    time_to_clear_best_ask_ns: float = 0.0
    time_to_clear_top5_ask_ns: float = 0.0

    ask_replenishment_rate: float = 0.0
    ask_replenishment_delay_ns: float = 0.0
    ask_replenishment_size_ratio: float = 0.0
    ask_replenishment_failure: float = 0.0
    post_trade_ask_repair_ns: float = 0.0
    depth_recovery_half_life_ns: float = 0.0

    bid_add_rate: float = 0.0
    bid_size_growth_rate: float = 0.0
    bid_depth_slope: float = 0.0
    bid_stack_persistence: float = 0.0
    bid_support_pressure: float = 0.0
    bid_replenishment_after_sell: float = 0.0

    aggressive_buy_count: float = 0.0
    aggressive_buy_size: float = 0.0
    aggressive_buy_notional: float = 0.0
    aggressive_buy_ratio: float = 0.0
    aggressive_buy_burst_score: float = 0.0
    buy_trade_interarrival_ns: float = 0.0
    buy_trade_clustering: float = 0.0
    sweep_trade_count: float = 0.0
    sweep_intensity: float = 0.0

    ask_cancel_rate: float = 0.0
    bid_cancel_rate: float = 0.0
    cancel_to_add_ratio_ask: float = 0.0
    cancel_to_add_ratio_bid: float = 0.0
    near_touch_cancel_rate: float = 0.0
    cancel_burst_score: float = 0.0
    cancel_asymmetry: float = 0.0

    total_ask_depth_1: float = 0.0
    total_ask_depth_3: float = 0.0
    total_ask_depth_5: float = 0.0
    total_ask_depth_10: float = 0.0
    ask_depth_slope: float = 0.0
    empty_levels_above_mid: float = 0.0
    thin_zone_width: float = 0.0
    depth_vacuum_score: float = 0.0

    median_order_age_bid_ns: float = 0.0
    median_order_age_ask_ns: float = 0.0
    order_age_compression: float = 0.0
    young_order_share: float = 0.0
    order_lifetime_asymmetry: float = 0.0

    best_ask_queue_length: float = 0.0
    best_bid_queue_length: float = 0.0
    queue_length_change: float = 0.0
    queue_collapse_score: float = 0.0
    queue_churn_rate: float = 0.0

    microprice_dislocation: float = 0.0
    microprice_velocity: float = 0.0
    microprice_acceleration: float = 0.0
    order_book_imbalance_1: float = 0.0
    order_book_imbalance_3: float = 0.0
    order_book_imbalance_5: float = 0.0
    order_book_imbalance_10: float = 0.0
    imbalance_persistence: float = 0.0

    depth_recovery_time_ns: float = 0.0
    spread_recovery_time_ns: float = 0.0
    book_resilience_decay: float = 0.0
    replenishment_half_life_ns: float = 0.0

    messages_per_second: float = 0.0
    add_rate: float = 0.0
    cancel_rate: float = 0.0
    modify_rate: float = 0.0
    trade_rate: float = 0.0
    event_acceleration: float = 0.0
    event_burst_score: float = 0.0
    interarrival_time_compression: float = 0.0

    trade_size_exceeds_displayed: float = 0.0
    iceberg_like_replenishment: float = 0.0
    print_through_expected_depth: float = 0.0

    spread_to_price: float = 0.0
    spread_duration_ns: float = 0.0
    spread_widening_before_sweep: float = 0.0
    spread_tightening_with_bid_stack: float = 0.0
    locked_or_crossed_flag: float = 0.0

    auction_pressure: float = 0.0
    auction_imbalance_velocity: float = 0.0
    auction_price_dislocation: float = 0.0

    venue_lead_lag_microprice: float = 0.0
    venue_trade_initiation_sequence: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {k: v for k, v in self.__dict__.items()}


class L3FeatureExtractor:
    def __init__(self, window_ns: int = 1_000_000_000):
        self._window_ns = window_ns
        self._events: deque[MBOEvent] = deque()
        self._snapshots: deque[BookSnapshot] = deque()
        self._ask_removed_history: deque[float] = deque(maxlen=100)
        self._ask_added_history: deque[float] = deque(maxlen=100)
        self._bid_removed_history: deque[float] = deque(maxlen=100)
        self._bid_added_history: deque[float] = deque(maxlen=100)
        self._trade_times: deque[int] = deque(maxlen=1000)
        self._buy_trade_times: deque[int] = deque(maxlen=1000)
        self._cancel_times: deque[int] = deque(maxlen=1000)
        self._microprice_history: deque[tuple[int, float]] = deque(maxlen=100)
        self._spread_history: deque[tuple[int, float]] = deque(maxlen=100)
        self._obi_history: deque[tuple[int, float]] = deque(maxlen=100)
        self._last_snapshot: BookSnapshot | None = None
        self._baseline_event_rate: float = 100.0

    def extract(self, events: list[MBOEvent], snapshots: list[BookSnapshot]) -> L3Features:
        if not events or not snapshots:
            return L3Features()

        ts_end = events[-1].ts_event_ns
        ts_start = ts_end - self._window_ns

        window_events = [e for e in events if e.ts_event_ns >= ts_start]
        window_snapshots = [s for s in snapshots if s.ts_ns >= ts_start]

        if not window_events:
            return L3Features()

        features = L3Features()
        self._compute_depletion_features(features, window_events, window_snapshots)
        self._compute_replenishment_features(features, window_events)
        self._compute_bid_support_features(features, window_events, window_snapshots)
        self._compute_aggressive_buy_features(features, window_events)
        self._compute_cancel_features(features, window_events)
        self._compute_depth_vacuum_features(features, window_snapshots)
        self._compute_order_age_features(features, window_events, window_snapshots)
        self._compute_queue_features(features, window_events, window_snapshots)
        self._compute_microprice_features(features, window_snapshots)
        self._compute_resilience_features(features, window_events, window_snapshots)
        self._compute_message_intensity_features(features, window_events)
        self._compute_hidden_liquidity_features(features, window_events, window_snapshots)
        self._compute_spread_features(features, window_snapshots, window_events)
        self._compute_auction_features(features, window_events)

        return features

    def _compute_depletion_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        ask_removed = sum(e.size for e in events if e.side == MBOSide.ASK and e.action in (MBOAction.CANCEL, MBOAction.EXECUTE, MBOAction.TRADE))
        bid_removed = sum(e.size for e in events if e.side == MBOSide.BID and e.action in (MBOAction.CANCEL, MBOAction.EXECUTE, MBOAction.TRADE))

        if snapshots:
            initial_ask_depth = snapshots[0].total_ask_depth_5 if snapshots[0].total_ask_depth_5 > 0 else 1
            initial_bid_depth = snapshots[0].total_bid_depth_5 if snapshots[0].total_bid_depth_5 > 0 else 1
            features.ask_depletion_ratio = min(ask_removed / initial_ask_depth, 5.0)
            features.bid_depletion_ratio = min(bid_removed / initial_bid_depth, 5.0)
            features.net_depletion_ratio = features.ask_depletion_ratio - features.bid_depletion_ratio

        trade_events = [e for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE) and e.side == MBOSide.ASK]
        features.top_ask_consumption = sum(e.size for e in trade_events)

        duration_ns = events[-1].ts_event_ns - events[0].ts_event_ns if len(events) > 1 else 1
        if duration_ns > 0:
            features.shares_removed_ask_per_ns = ask_removed / duration_ns
            features.price_levels_cleared_per_sec = (ask_removed / 100.0) / (duration_ns / 1e9)

    def _compute_replenishment_features(self, features: L3Features, events: list[MBOEvent]):
        ask_adds = [e for e in events if e.side == MBOSide.ASK and e.action == MBOAction.ADD]
        ask_removes = [e for e in events if e.side == MBOSide.ASK and e.action in (MBOAction.CANCEL, MBOAction.EXECUTE, MBOAction.TRADE)]

        if ask_removes:
            features.ask_replenishment_rate = len(ask_adds) / len(ask_removes) if ask_removes else 0.0
            total_removed = sum(e.size for e in ask_removes)
            total_added = sum(e.size for e in ask_adds)
            features.ask_replenishment_size_ratio = total_added / total_removed if total_removed > 0 else 0.0
            features.ask_replenishment_failure = max(0.0, 1.0 - features.ask_replenishment_size_ratio)

        if ask_adds and ask_removes:
            delays = []
            for rem in ask_removes[:10]:
                for add in ask_adds:
                    if add.ts_event_ns > rem.ts_event_ns:
                        delays.append(add.ts_event_ns - rem.ts_event_ns)
                        break
            if delays:
                features.ask_replenishment_delay_ns = np.median(delays)

    def _compute_bid_support_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        bid_adds = [e for e in events if e.side == MBOSide.BID and e.action == MBOAction.ADD]
        bid_cancels = [e for e in events if e.side == MBOSide.BID and e.action == MBOAction.CANCEL]

        duration_ns = events[-1].ts_event_ns - events[0].ts_event_ns if len(events) > 1 else 1
        if duration_ns > 0:
            features.bid_add_rate = len(bid_adds) / (duration_ns / 1e9)

        if snapshots and len(snapshots) > 1:
            initial_depth = snapshots[0].total_bid_depth_5
            final_depth = snapshots[-1].total_bid_depth_5
            if initial_depth > 0:
                features.bid_size_growth_rate = (final_depth - initial_depth) / initial_depth

        add_size = sum(e.size for e in bid_adds)
        cancel_size = sum(e.size for e in bid_cancels)
        features.bid_support_pressure = (add_size - cancel_size) / (add_size + cancel_size) if (add_size + cancel_size) > 0 else 0.0

    def _compute_aggressive_buy_features(self, features: L3Features, events: list[MBOEvent]):
        buy_trades = [e for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE) and e.side == MBOSide.BID]
        all_trades = [e for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE)]

        features.aggressive_buy_count = len(buy_trades)
        features.aggressive_buy_size = sum(e.size for e in buy_trades)
        features.aggressive_buy_notional = sum(e.size * e.price for e in buy_trades)
        features.aggressive_buy_ratio = len(buy_trades) / len(all_trades) if all_trades else 0.0

        if len(buy_trades) > 1:
            interarrivals = [buy_trades[i+1].ts_event_ns - buy_trades[i].ts_event_ns for i in range(len(buy_trades)-1)]
            features.buy_trade_interarrival_ns = np.median(interarrivals)
            if len(interarrivals) > 2:
                features.buy_trade_clustering = np.std(interarrivals) / np.mean(interarrivals) if np.mean(interarrivals) > 0 else 0.0

        sweeps = [e for e in buy_trades if e.size > 1000]
        features.sweep_trade_count = len(sweeps)
        if len(buy_trades) > 0:
            features.sweep_intensity = len(sweeps) / len(buy_trades)

    def _compute_cancel_features(self, features: L3Features, events: list[MBOEvent]):
        ask_cancels = [e for e in events if e.side == MBOSide.ASK and e.action == MBOAction.CANCEL]
        bid_cancels = [e for e in events if e.side == MBOSide.BID and e.action == MBOAction.CANCEL]
        ask_adds = [e for e in events if e.side == MBOSide.ASK and e.action == MBOAction.ADD]
        bid_adds = [e for e in events if e.side == MBOSide.BID and e.action == MBOAction.ADD]

        duration_ns = events[-1].ts_event_ns - events[0].ts_event_ns if len(events) > 1 else 1
        if duration_ns > 0:
            features.ask_cancel_rate = len(ask_cancels) / (duration_ns / 1e9)
            features.bid_cancel_rate = len(bid_cancels) / (duration_ns / 1e9)

        features.cancel_to_add_ratio_ask = len(ask_cancels) / len(ask_adds) if ask_adds else 0.0
        features.cancel_to_add_ratio_bid = len(bid_cancels) / len(bid_adds) if bid_adds else 0.0

        ask_cancel_size = sum(e.size for e in ask_cancels)
        bid_cancel_size = sum(e.size for e in bid_cancels)
        total_cancel = ask_cancel_size + bid_cancel_size
        features.cancel_asymmetry = (ask_cancel_size - bid_cancel_size) / total_cancel if total_cancel > 0 else 0.0

        if len(ask_cancels) > 2:
            cancel_times = [ask_cancels[i+1].ts_event_ns - ask_cancels[i].ts_event_ns for i in range(len(ask_cancels)-1)]
            features.cancel_burst_score = np.std(cancel_times) / np.mean(cancel_times) if np.mean(cancel_times) > 0 else 0.0

    def _compute_depth_vacuum_features(self, features: L3Features, snapshots: list[BookSnapshot]):
        if not snapshots:
            return
        snap = snapshots[-1]
        features.total_ask_depth_1 = snap.total_ask_depth_1
        features.total_ask_depth_3 = snap.total_ask_depth_3
        features.total_ask_depth_5 = snap.total_ask_depth_5
        features.total_ask_depth_10 = snap.total_ask_depth_10

        if len(snap.ask_levels) > 1:
            depths = [l.total_size for l in snap.ask_levels[:5]]
            if len(depths) > 1 and depths[0] > 0:
                features.ask_depth_slope = (depths[-1] - depths[0]) / len(depths) / depths[0]

        if snap.total_ask_depth_1 > 0:
            inverse_depth = 1.0 / snap.total_ask_depth_1
            empty_levels = max(0, 10 - len(snap.ask_levels))
            slope_decay = max(0, -features.ask_depth_slope)
            features.depth_vacuum_score = (inverse_depth + empty_levels / 10.0 + slope_decay) / 3.0

    def _compute_order_age_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        if not snapshots or not snapshots[-1].bid_levels or not snapshots[-1].ask_levels:
            return

        bid_ages = []
        ask_ages = []
        for level in snapshots[-1].bid_levels[:5]:
            for order in level.orders:
                age = events[-1].ts_event_ns - order.add_ts_ns
                bid_ages.append(age)

        for level in snapshots[-1].ask_levels[:5]:
            for order in level.orders:
                age = events[-1].ts_event_ns - order.add_ts_ns
                ask_ages.append(age)

        if bid_ages:
            features.median_order_age_bid_ns = np.median(bid_ages)
        if ask_ages:
            features.median_order_age_ask_ns = np.median(ask_ages)

        all_ages = bid_ages + ask_ages
        if all_ages:
            young_threshold = 1_000_000_000
            young_count = sum(1 for a in all_ages if a < young_threshold)
            features.young_order_share = young_count / len(all_ages)

    def _compute_queue_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        if not snapshots:
            return
        snap = snapshots[-1]
        features.best_ask_queue_length = snap.total_ask_depth_1
        features.best_bid_queue_length = snap.total_bid_depth_1

        if len(snapshots) > 1:
            features.queue_length_change = snap.total_ask_depth_1 - snapshots[0].total_ask_depth_1

        if snapshots[0].total_ask_depth_1 > 0:
            features.queue_collapse_score = 1.0 - (snap.total_ask_depth_1 / snapshots[0].total_ask_depth_1)

    def _compute_microprice_features(self, features: L3Features, snapshots: list[BookSnapshot]):
        if not snapshots:
            return
        snap = snapshots[-1]
        features.microprice_dislocation = snap.microprice - snap.midprice
        features.order_book_imbalance_1 = snap.order_book_imbalance

        if len(snap.bid_levels) >= 3 and len(snap.ask_levels) >= 3:
            bid_depth_3 = snap.total_bid_depth_3
            ask_depth_3 = snap.total_ask_depth_3
            if bid_depth_3 + ask_depth_3 > 0:
                features.order_book_imbalance_3 = (bid_depth_3 - ask_depth_3) / (bid_depth_3 + ask_depth_3)

        if len(self._microprice_history) > 1:
            times = [t for t, _ in self._microprice_history]
            prices = [p for _, p in self._microprice_history]
            if len(times) > 1:
                dt = times[-1] - times[0]
                if dt > 0:
                    features.microprice_velocity = (prices[-1] - prices[0]) / (dt / 1e9)

    def _compute_resilience_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        if len(snapshots) < 2:
            return

        for i in range(1, len(snapshots)):
            if snapshots[i-1].total_ask_depth_1 > snapshots[i].total_ask_depth_1:
                recovery_time = snapshots[i].ts_ns - snapshots[i-1].ts_ns
                features.depth_recovery_time_ns = recovery_time
                break

        if features.depth_recovery_time_ns > 0:
            features.replenishment_half_life_ns = features.depth_recovery_time_ns / 2.0

    def _compute_message_intensity_features(self, features: L3Features, events: list[MBOEvent]):
        if len(events) < 2:
            return

        duration_ns = events[-1].ts_event_ns - events[0].ts_event_ns
        if duration_ns > 0:
            features.messages_per_second = len(events) / (duration_ns / 1e9)

        adds = sum(1 for e in events if e.action == MBOAction.ADD)
        cancels = sum(1 for e in events if e.action == MBOAction.CANCEL)
        modifies = sum(1 for e in events if e.action == MBOAction.MODIFY)
        trades = sum(1 for e in events if e.action in (MBOAction.TRADE, MBOAction.EXECUTE))

        if duration_ns > 0:
            features.add_rate = adds / (duration_ns / 1e9)
            features.cancel_rate = cancels / (duration_ns / 1e9)
            features.modify_rate = modifies / (duration_ns / 1e9)
            features.trade_rate = trades / (duration_ns / 1e9)

        if self._baseline_event_rate > 0:
            features.event_acceleration = features.messages_per_second / self._baseline_event_rate

        if len(events) > 10:
            interarrivals = [events[i+1].ts_event_ns - events[i].ts_event_ns for i in range(len(events)-1)]
            features.interarrival_time_compression = np.std(interarrivals) / np.mean(interarrivals) if np.mean(interarrivals) > 0 else 0.0

    def _compute_hidden_liquidity_features(self, features: L3Features, events: list[MBOEvent], snapshots: list[BookSnapshot]):
        if not snapshots:
            return

        for trade in events:
            if trade.action in (MBOAction.TRADE, MBOAction.EXECUTE):
                snap = next((s for s in snapshots if abs(s.ts_ns - trade.ts_event_ns) < 1_000_000), None)
                if snap:
                    displayed = snap.total_ask_depth_1 if trade.side == MBOSide.BID else snap.total_bid_depth_1
                    if trade.size > displayed and displayed > 0:
                        features.trade_size_exceeds_displayed += 1

        if len(events) > 0:
            features.trade_size_exceeds_displayed /= len(events)

    def _compute_spread_features(self, features: L3Features, snapshots: list[BookSnapshot], events: list[MBOEvent]):
        if not snapshots:
            return
        snap = snapshots[-1]
        if snap.midprice > 0:
            features.spread_to_price = snap.spread / snap.midprice

        features.locked_or_crossed_flag = 1.0 if snap.best_bid >= snap.best_ask else 0.0

    def _compute_auction_features(self, features: L3Features, events: list[MBOEvent]):
        auction_events = [e for e in events if e.action == MBOAction.AUCTION]
        if not auction_events:
            return

        last_auction = auction_events[-1]
        features.auction_pressure = last_auction.size * abs(last_auction.price - (last_auction.price * 0.99))
