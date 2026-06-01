"""Crypto-specific execution quality metrics — slippage, adverse selection, queue quality."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np


def slippage_bps(
    fill_price: float,
    reference_price: float,
    side: str,
) -> float:
    """Compute slippage in basis points.

    Measures fill price vs reference price at intent time.
    Positive = adverse slippage (worse than quoted), negative = favorable.

    For BUY: (fill_price - reference_price) / reference_price * 10000
    For SELL: (reference_price - fill_price) / reference_price * 10000
    """
    if reference_price <= 0:
        return 0.0
    if side.upper() == "BUY":
        return (fill_price - reference_price) / reference_price * 10000.0
    return (reference_price - fill_price) / reference_price * 10000.0


def compute_pnl_trade(
    buy_price: float,
    sell_price: float,
    quantity: float,
) -> float:
    """Compute PnL for a round-trip trade."""
    return (sell_price - buy_price) * quantity


def jump_metric(
    fill_events: List[Dict[str, Any]],
    subsequent_mid_prices: List[float],
    n_ticks: int = 5,
) -> float:
    """Adverse selection measure — how much price moves against fills.

    For each fill event, computes the mid-price change over the next N ticks,
    multiplied by side direction (+1 for BUY, -1 for SELL). Positive values
    mean fills benefited from subsequent price movement.

    Args:
        fill_events: List of fill event dicts with 'side', 'avg_fill_price', 'filled_quantity'.
        subsequent_mid_prices: Mid-prices after each fill, concatenated per fill.
        n_ticks: Number of post-fill ticks to examine per fill.

    Returns:
        Mean jump metric in basis points across all fills. Negative = adverse selection.
    """
    if not fill_events or len(subsequent_mid_prices) < n_ticks:
        return 0.0

    jumps: List[float] = []
    idx = 0

    for ev in fill_events:
        fill_price = float(ev.get("avg_fill_price", 0))
        side = ev.get("side", "BUY")
        if fill_price <= 0:
            continue

        post_prices = subsequent_mid_prices[idx: idx + n_ticks]
        idx += n_ticks
        if len(post_prices) < n_ticks:
            break

        mid_change = (post_prices[-1] - fill_price) / fill_price * 10000.0
        side_sign = 1.0 if side.upper() == "BUY" else -1.0
        jumps.append(side_sign * mid_change)

    return float(np.mean(jumps)) if jumps else 0.0


def qqe_metric(
    order_book_depth: List[Dict[str, Any]],
    side: str,
    price_level: float,
    tick_size: float,
) -> float:
    """Queue Quality Estimate — approximate queue position from L2 depth.

    Higher value = closer to front of queue (better fill probability).

    For L2 data, estimates queue position from volume-weighted depth:
      - Finds the total volume at the target price level (cumulative across bids/asks)
      - Finds the median volume across all price levels on that side
      - qqe = max(0, 1 - level_volume / max(median_volume, level_volume))
      - Empty level → 1.0 (front of queue). No data → 0.5 (neutral).

    For L3 data, set order_book_depth entries with 'order_sequence' field:
      qqe = 1 - (position_in_queue / total_orders)

    Range: 0.0 (back of queue) to 1.0 (front of queue/empty level).
    """
    if not order_book_depth:
        return 1.0

    level_volume = 0.0
    all_volumes: List[float] = []

    for level in order_book_depth:
        qty = float(level.get("qty", 0.0))
        price = float(level.get("price", 0))
        is_target = abs(price - price_level) < tick_size if price > 0 else False

        if is_target:
            if "order_sequence" in level:
                total_orders = float(level.get("total_orders", 1))
                seq = float(level.get("order_sequence", 0))
                return 1.0 - (seq / max(total_orders, 1))
            level_volume = qty

        if qty > 0:
            all_volumes.append(qty)

    if not all_volumes:
        return 1.0

    if level_volume <= 0:
        return 1.0

    median_vol = float(np.median(all_volumes))
    if median_vol <= 0:
        return 0.5

    return max(0.0, 1.0 - level_volume / max(median_vol, level_volume))


def compute_execution_quality(
    fill_events: List[Dict[str, Any]],
    order_book_snapshots: Optional[List[Dict[str, Any]]] = None,
    subsequent_mid_prices: Optional[List[float]] = None,
    tick_size: float = 0.1,
) -> Dict[str, float]:
    """Aggregate execution quality metrics across all fill events.

    Returns dict with: mean_slippage_bps, mean_jump_bps, mean_qqe.
    """
    result: Dict[str, float] = {
        "mean_slippage_bps": 0.0,
        "mean_jump_bps": 0.0,
        "mean_qqe": 0.0,
    }

    if not fill_events:
        return result

    slps: List[float] = []
    qqes: List[float] = []

    for i, ev in enumerate(fill_events):
        fill_price = float(ev.get("avg_fill_price", 0))
        ref_price = float(ev.get("reference_price", fill_price))
        side = ev.get("side", "BUY")

        if ref_price > 0 and fill_price > 0:
            slps.append(slippage_bps(fill_price, ref_price, side))

        if order_book_snapshots and i < len(order_book_snapshots):
            snap = order_book_snapshots[i]
            qqes.append(qqe_metric(
                snap.get("depth", []),
                side,
                fill_price,
                tick_size,
            ))

    if slps:
        result["mean_slippage_bps"] = float(np.mean(slps))
    if qqes:
        result["mean_qqe"] = float(np.mean(qqes))

    if subsequent_mid_prices:
        result["mean_jump_bps"] = jump_metric(fill_events, subsequent_mid_prices)

    return result
