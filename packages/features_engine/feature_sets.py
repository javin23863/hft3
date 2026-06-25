"""Research-only microstructure feature functions.

These helpers operate on explicit point-in-time depth snapshots or trailing
trade windows. They do not write to the C++ FeatureIndex hot path.

Receipts:
- Queue imbalance: https://arxiv.org/abs/1512.03492
- Order-book imbalance example: https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html
- Microstructure feature families: https://arxiv.org/html/2602.00776v1
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

MICROSTRUCTURE_FEATURE_RECEIPTS = {
    "decision_time_boundary": "single depth snapshot at decision time t or trailing trade window ending at t",
    "feature_family": "microstructure_order_book",
    "hot_path_status": "research_only_not_feature_index",
    "receipts": {
        "queue_imbalance": "https://arxiv.org/abs/1512.03492",
        "order_book_imbalance": "https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html",
        "microstructure_features": "https://arxiv.org/html/2602.00776v1",
    },
    "features": {
        "order_book_imbalance": "(sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)",
        "queue_imbalance": "(best_bid_qty - best_ask_qty) / (best_bid_qty + best_ask_qty)",
        "order_flow_imbalance": "(buy_qty - sell_qty) / (buy_qty + sell_qty)",
        "micro_price": "(best_bid_price * ask_qty + best_ask_price * bid_qty) / (bid_qty + ask_qty)",
        "vwap_to_mid_deviation": "(vwap - midpoint) / midpoint",
        "spread": "best_ask_price - best_bid_price",
        "weighted_depth_price": "volume weighted average price across book levels",
        "vamp": "cross-side volume adjusted market price across depth levels",
    },
}


def order_book_imbalance(snapshot: Mapping[str, Any], depth: int | None = None) -> float:
    """Return order-book imbalance in [-1, 1] for snapshot t."""
    bids = _levels(snapshot, "bids", depth)
    asks = _levels(snapshot, "asks", depth)
    return _safe_imbalance(
        sum(qty for _price, qty in bids),
        sum(qty for _price, qty in asks),
    )


def queue_imbalance(snapshot: Mapping[str, Any]) -> float:
    """Return best-level queue imbalance in [-1, 1] for snapshot t."""
    bids = _levels(snapshot, "bids", 1)
    asks = _levels(snapshot, "asks", 1)
    bid_qty = bids[0][1] if bids else 0.0
    ask_qty = asks[0][1] if asks else 0.0
    return _safe_imbalance(bid_qty, ask_qty)


def order_flow_imbalance(trades_window: Sequence[Mapping[str, Any] | Sequence[Any]]) -> float:
    """Return trailing signed trade-volume imbalance in [-1, 1].

    Trade rows may be mappings with side/aggressor plus size/qty/quantity,
    tuples like ``(side, qty)``, or priced tuples like ``(side, price, qty)``.
    Buy sides are ``buy``, ``b``, ``bid``, ``1``; sell sides are ``sell``,
    ``s``, ``ask``, ``-1``.
    """
    buy_qty = 0.0
    sell_qty = 0.0
    for raw in _trade_rows(trades_window):
        side, qty = _trade_side_qty(raw)
        if side == "buy":
            buy_qty += qty
        elif side == "sell":
            sell_qty += qty
    return _safe_imbalance(buy_qty, sell_qty)


def micro_price(snapshot: Mapping[str, Any]) -> float:
    """Return best-level micro-price, or 0.0 when either side is unavailable."""
    bids = _levels(snapshot, "bids", 1)
    asks = _levels(snapshot, "asks", 1)
    if not bids or not asks:
        return 0.0
    bid_price, bid_qty = bids[0]
    ask_price, ask_qty = asks[0]
    denom = bid_qty + ask_qty
    if denom <= 0.0:
        return 0.0
    return (bid_price * ask_qty + ask_price * bid_qty) / denom


def vwap_to_mid_deviation(
    trades_window: Sequence[Mapping[str, Any] | Sequence[Any]],
    midpoint: float,
) -> float:
    """Return trailing VWAP deviation from current midpoint."""
    midpoint = _finite_number(midpoint, "midpoint")
    if midpoint == 0.0:
        return 0.0
    numerator = 0.0
    denominator = 0.0
    for raw in _trade_rows(trades_window):
        price, qty = _trade_price_qty(raw)
        numerator += price * qty
        denominator += qty
    if denominator <= 0.0:
        return 0.0
    return ((numerator / denominator) - midpoint) / midpoint


def spread(snapshot: Mapping[str, Any], *, relative: bool = False) -> float:
    """Return best ask minus best bid, optionally divided by midpoint."""
    bids = _levels(snapshot, "bids", 1)
    asks = _levels(snapshot, "asks", 1)
    if not bids or not asks:
        return 0.0
    value = asks[0][0] - bids[0][0]
    if not relative:
        return value
    midpoint = (asks[0][0] + bids[0][0]) / 2.0
    return value / midpoint if midpoint else 0.0


def weighted_depth_price(
    snapshot: Mapping[str, Any],
    *,
    side: str | None = None,
    depth: int | None = None,
) -> float:
    """Return same-side or full-book volume weighted depth price."""
    if side is None:
        levels = _levels(snapshot, "bids", depth) + _levels(snapshot, "asks", depth)
    else:
        levels = _levels(snapshot, _side_key(side), depth)
    numerator = sum(price * qty for price, qty in levels)
    denominator = sum(qty for _price, qty in levels)
    return numerator / denominator if denominator > 0.0 else 0.0


def vamp(snapshot: Mapping[str, Any], depth: int | None = None) -> float:
    """Return cross-side volume adjusted market price across aligned depth."""
    bids = _levels(snapshot, "bids", depth)
    asks = _levels(snapshot, "asks", depth)
    if not bids or not asks:
        return 0.0
    numerator = 0.0
    denominator = 0.0
    for (bid_price, bid_qty), (ask_price, ask_qty) in zip(bids, asks):
        numerator += ask_price * bid_qty + bid_price * ask_qty
        denominator += bid_qty + ask_qty
    return numerator / denominator if denominator > 0.0 else 0.0


def microstructure_feature_packet(
    snapshot: Mapping[str, Any],
    *,
    trades_window: Sequence[Mapping[str, Any] | Sequence[Any]] | None = None,
    midpoint: float | None = None,
    depth: int | None = None,
) -> dict[str, float]:
    """Return all snapshot features plus optional trailing-window features."""
    packet = {
        "order_book_imbalance": order_book_imbalance(snapshot, depth=depth),
        "queue_imbalance": queue_imbalance(snapshot),
        "micro_price": micro_price(snapshot),
        "spread": spread(snapshot),
        "relative_spread": spread(snapshot, relative=True),
        "weighted_depth_price": weighted_depth_price(snapshot, depth=depth),
        "vamp": vamp(snapshot, depth=depth),
    }
    if trades_window is not None:
        packet["order_flow_imbalance"] = order_flow_imbalance(trades_window)
        if midpoint is not None:
            packet["vwap_to_mid_deviation"] = vwap_to_mid_deviation(trades_window, midpoint)
    return packet


def _levels(snapshot: Mapping[str, Any], side: str, depth: int | None) -> list[tuple[float, float]]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be a mapping")
    if depth is not None and depth <= 0:
        raise ValueError("depth must be positive when provided")
    raw_levels = snapshot.get(side, [])
    if isinstance(raw_levels, Mapping):
        iterable = raw_levels.items()
    elif isinstance(raw_levels, Sequence) and not isinstance(raw_levels, (str, bytes)):
        iterable = raw_levels
    else:
        raise ValueError(f"{side} levels must be a sequence or mapping")
    levels: list[tuple[float, float]] = []
    for raw in iterable:
        price, qty = _level_price_qty(raw)
        if qty > 0.0:
            levels.append((price, qty))
    levels.sort(key=lambda item: item[0], reverse=(side == "bids"))
    return levels[:depth] if depth is not None else levels


def _level_price_qty(raw: Any) -> tuple[float, float]:
    if isinstance(raw, Mapping):
        price = raw.get("price")
        qty = raw.get("size", raw.get("qty", raw.get("quantity")))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 2:
        price, qty = raw[0], raw[1]
    else:
        raise ValueError(f"invalid depth level {raw!r}")
    parsed_price = _finite_number(price, "depth price")
    parsed_qty = _finite_number(qty, "depth quantity")
    if parsed_price <= 0.0:
        raise ValueError(f"non-positive depth price {raw!r}")
    if parsed_qty < 0.0:
        raise ValueError(f"negative depth quantity {raw!r}")
    return parsed_price, parsed_qty


def _trade_rows(trades_window: Sequence[Mapping[str, Any] | Sequence[Any]]) -> list[Mapping[str, Any] | Sequence[Any]]:
    if isinstance(trades_window, (str, bytes)) or not isinstance(trades_window, Sequence):
        raise ValueError("trades_window must be a sequence")
    return list(trades_window)


def _trade_side_qty(raw: Mapping[str, Any] | Sequence[Any]) -> tuple[str, float]:
    if isinstance(raw, Mapping):
        side_value = raw.get("side", raw.get("aggressor_side", raw.get("direction")))
        qty_value = raw.get("size", raw.get("qty", raw.get("quantity", raw.get("volume"))))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 2:
        side_value = raw[0]
        qty_value = raw[2] if len(raw) >= 3 else raw[1]
    else:
        raise ValueError(f"invalid trade row {raw!r}")
    side = _normalise_trade_side(side_value)
    qty = _finite_number(qty_value, "trade quantity")
    if qty < 0.0:
        raise ValueError(f"negative trade quantity {raw!r}")
    return side, qty


def _trade_price_qty(raw: Mapping[str, Any] | Sequence[Any]) -> tuple[float, float]:
    if isinstance(raw, Mapping):
        price_value = raw.get("price")
        qty_value = raw.get("size", raw.get("qty", raw.get("quantity", raw.get("volume"))))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) >= 3:
        price_value, qty_value = raw[1], raw[2]
    else:
        raise ValueError(f"invalid priced trade row {raw!r}")
    price = _finite_number(price_value, "trade price")
    qty = _finite_number(qty_value, "trade quantity")
    if qty < 0.0:
        raise ValueError(f"negative trade quantity {raw!r}")
    return price, qty


def _normalise_trade_side(value: Any) -> str:
    text = str(value).strip().lower()
    if text in {"buy", "b", "bid", "1", "+1"}:
        return "buy"
    if text in {"sell", "s", "ask", "-1"}:
        return "sell"
    raise ValueError(f"unknown trade side {value!r}")


def _finite_number(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite numeric")
    return parsed


def _safe_imbalance(left: float, right: float) -> float:
    denom = left + right
    if denom <= 0.0:
        return 0.0
    return (left - right) / denom


def _side_key(side: str) -> str:
    normalised = side.lower()
    if normalised in {"bid", "bids"}:
        return "bids"
    if normalised in {"ask", "asks"}:
        return "asks"
    raise ValueError("side must be bids, asks, bid, ask, or None")
