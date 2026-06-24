"""Research-only microstructure feature functions.

These helpers operate on a single point-in-time depth snapshot and do not write
to the C++/FeatureIndex hot path. A snapshot is a mapping with ``bids`` and
``asks`` lists. Each level may be ``(price, size)`` or a mapping with
``price`` plus ``size``/``qty``/``quantity``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

MICROSTRUCTURE_FEATURE_RECEIPTS = {
    "decision_time_boundary": "single depth snapshot at decision time t",
    "feature_family": "microstructure_order_book",
    "hot_path_status": "research_only_not_feature_index",
    "features": {
        "order_book_imbalance": "(sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)",
        "queue_imbalance": "(best_bid_qty - best_ask_qty) / (best_bid_qty + best_ask_qty)",
        "micro_price": "(best_ask_price * best_bid_qty + best_bid_price * best_ask_qty) / (best_bid_qty + best_ask_qty)",
        "vamp": "cross-side volume adjusted price across depth levels",
        "weighted_depth_price": "same-side volume weighted depth price",
    },
}


def order_book_imbalance(snapshot: Mapping[str, Any], depth: int | None = None) -> float:
    """Return static order-book imbalance in [-1, 1] for snapshot t."""
    bids = _levels(snapshot, "bids", depth)
    asks = _levels(snapshot, "asks", depth)
    bid_qty = sum(qty for _price, qty in bids)
    ask_qty = sum(qty for _price, qty in asks)
    return _safe_imbalance(bid_qty, ask_qty)


def queue_imbalance(snapshot: Mapping[str, Any]) -> float:
    """Return best-level queue imbalance in [-1, 1] for snapshot t."""
    bids = _levels(snapshot, "bids", 1)
    asks = _levels(snapshot, "asks", 1)
    bid_qty = bids[0][1] if bids else 0.0
    ask_qty = asks[0][1] if asks else 0.0
    return _safe_imbalance(bid_qty, ask_qty)


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
    return (ask_price * bid_qty + bid_price * ask_qty) / denom


def vamp(snapshot: Mapping[str, Any], depth: int | None = None) -> float:
    """Return cross-side volume adjusted market price over aligned depth levels."""
    bids = _levels(snapshot, "bids", depth)
    asks = _levels(snapshot, "asks", depth)
    if not bids or not asks:
        return 0.0
    n = min(len(bids), len(asks))
    numerator = 0.0
    denominator = 0.0
    for idx in range(n):
        bid_price, bid_qty = bids[idx]
        ask_price, ask_qty = asks[idx]
        numerator += ask_price * bid_qty + bid_price * ask_qty
        denominator += bid_qty + ask_qty
    return numerator / denominator if denominator > 0.0 else 0.0


def weighted_depth_price(
    snapshot: Mapping[str, Any],
    *,
    side: str | None = None,
    depth: int | None = None,
) -> float:
    """Return same-side or all-book volume weighted depth price."""
    if side is None:
        levels = _levels(snapshot, "bids", depth) + _levels(snapshot, "asks", depth)
    else:
        side_key = _side_key(side)
        levels = _levels(snapshot, side_key, depth)
    numerator = sum(price * qty for price, qty in levels)
    denominator = sum(qty for _price, qty in levels)
    return numerator / denominator if denominator > 0.0 else 0.0


def microstructure_feature_packet(snapshot: Mapping[str, Any], depth: int | None = None) -> dict[str, float]:
    """Return all research-only microstructure features for a snapshot."""
    return {
        "order_book_imbalance": order_book_imbalance(snapshot, depth=depth),
        "queue_imbalance": queue_imbalance(snapshot),
        "micro_price": micro_price(snapshot),
        "vamp": vamp(snapshot, depth=depth),
        "weighted_depth_price": weighted_depth_price(snapshot, depth=depth),
    }


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
    parsed_price = float(price)
    parsed_qty = float(qty)
    if not math.isfinite(parsed_price) or not math.isfinite(parsed_qty):
        raise ValueError(f"non-finite depth level {raw!r}")
    if parsed_price <= 0.0:
        raise ValueError(f"non-positive depth price {raw!r}")
    if parsed_qty < 0.0:
        raise ValueError(f"negative depth quantity {raw!r}")
    return parsed_price, parsed_qty


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
