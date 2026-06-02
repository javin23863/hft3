"""Order-flow imbalance: true OFI, proxy, or trade-pressure-only."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from features_engine.src.imbalance.classification import DataClass, feature_family_for_data_class
from features_engine.src.imbalance.windows import MultiWindowAggregator
from features_engine.src.structural_models.model_01_book_pressure import compute_level1_ofi_event


@dataclass
class OrderFlowState:
    buy_agg_vol: float = 0.0
    sell_agg_vol: float = 0.0
    bid_add_vol: float = 0.0
    ask_add_vol: float = 0.0
    bid_cancel_vol: float = 0.0
    ask_cancel_vol: float = 0.0
    bid_depletion: float = 0.0
    ask_depletion: float = 0.0
    prev_bid_p: float = 0.0
    prev_bid_q: int = 0
    prev_ask_p: float = float("inf")
    prev_ask_q: int = 0


@dataclass
class OrderFlowSnapshot:
    feature_family: str
    ofi_l1: float
    signed_trade_pressure: float
    net_liquidity_added: float
    net_liquidity_removed: float
    window_sums: Dict[str, float] = field(default_factory=dict)
    components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_family": self.feature_family,
            "ofi_l1": self.ofi_l1,
            "signed_trade_pressure": self.signed_trade_pressure,
            "net_liquidity_added": self.net_liquidity_added,
            "net_liquidity_removed": self.net_liquidity_removed,
            "window_sums": dict(self.window_sums),
            "components": dict(self.components),
        }


class OrderFlowImbalanceEngine:
    def __init__(
        self,
        data_class: DataClass,
        *,
        window_ms: Optional[List[int]] = None,
    ) -> None:
        self.data_class = data_class
        self.feature_family = feature_family_for_data_class(data_class)
        self._state = OrderFlowState()
        self._windows = MultiWindowAggregator.from_config(window_ms)
        self._prev_depth_bid = 0.0
        self._prev_depth_ask = 0.0

    def can_emit_true_ofi(self) -> bool:
        return self.data_class == DataClass.MBO

    def on_mbo_trade(self, side: str, size: int) -> None:
        if side == "A":
            self._state.buy_agg_vol += size
        else:
            self._state.sell_agg_vol += size

    def on_mbo_add(self, side: str, size: int) -> None:
        if side == "B":
            self._state.bid_add_vol += size
        else:
            self._state.ask_add_vol += size

    def on_mbo_cancel(self, side: str, size: int) -> None:
        if side == "B":
            self._state.bid_cancel_vol += size
        else:
            self._state.ask_cancel_vol += size

    def on_bbo(
        self,
        ts_ns: int,
        bid_p: float,
        bid_q: int,
        ask_p: float,
        ask_q: int,
        *,
        depth_bid: Optional[float] = None,
        depth_ask: Optional[float] = None,
    ) -> OrderFlowSnapshot:
        ofi = math.nan
        if self.can_emit_true_ofi():
            ofi = compute_level1_ofi_event(
                self._state.prev_bid_p,
                self._state.prev_bid_q,
                self._state.prev_ask_p,
                self._state.prev_ask_q,
                bid_p,
                bid_q,
                ask_p,
                ask_q,
            )
            self._state.prev_bid_p = bid_p
            self._state.prev_bid_q = bid_q
            self._state.prev_ask_p = ask_p
            self._state.prev_ask_q = ask_q
        elif self.data_class in (DataClass.MBP_10, DataClass.MBP_1):
            db = depth_bid if depth_bid is not None else float(bid_q)
            da = depth_ask if depth_ask is not None else float(ask_q)
            ofi = (db - self._prev_depth_bid) - (da - self._prev_depth_ask)
            self._prev_depth_bid = db
            self._prev_depth_ask = da
            self.feature_family = "order_flow_imbalance_proxy"

        total_agg = self._state.buy_agg_vol + self._state.sell_agg_vol
        trade_pressure = (
            (self._state.buy_agg_vol - self._state.sell_agg_vol) / total_agg
            if total_agg > 0
            else 0.0
        )
        if self.data_class == DataClass.TRADES:
            self.feature_family = "trade_pressure_only"
            ofi = math.nan

        net_added = self._state.bid_add_vol + self._state.ask_add_vol
        net_removed = self._state.bid_cancel_vol + self._state.ask_cancel_vol

        if not math.isnan(ofi):
            self._windows.push(ts_ns, ofi)

        return OrderFlowSnapshot(
            feature_family=self.feature_family,
            ofi_l1=ofi,
            signed_trade_pressure=trade_pressure,
            net_liquidity_added=net_added,
            net_liquidity_removed=net_removed,
            window_sums=self._windows.sums(ts_ns),
            components={
                "bid_additions": self._state.bid_add_vol,
                "ask_additions": self._state.ask_add_vol,
                "bid_cancellations": self._state.bid_cancel_vol,
                "ask_cancellations": self._state.ask_cancel_vol,
                "aggressive_buy_volume": self._state.buy_agg_vol,
                "aggressive_sell_volume": self._state.sell_agg_vol,
            },
        )


def assert_no_true_ofi_when_insufficient(data_class: DataClass, ofi_value: float) -> None:
    if data_class != DataClass.MBO and not math.isnan(ofi_value):
        raise ValueError(
            f"true OFI emitted for data_class={data_class.value}; use proxy or trade_pressure_only"
        )
