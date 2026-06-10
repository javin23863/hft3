"""Execution simulation: latency, slippage, fees, halts."""
from __future__ import annotations

from dataclasses import dataclass

from equities_lane.src.types import ExecutionConfig, LATENCY_FLOOR_MS, TradeFill


@dataclass
class OrderIntent:
    ts_ns: int
    side: str
    qty: int
    limit_price: float | None = None


def simulate_fill(
    intent: OrderIntent,
    bid_px: float,
    ask_px: float,
    config: ExecutionConfig,
    *,
    halted: bool = False,
) -> TradeFill | None:
    if halted and config.halt_on_limit_up:
        return None
    # Clamp at point of use so swept/cloned configs are also guarded.
    # Anti-lookahead/optimistic-alpha guard: fills cannot be faster than the floor.
    latency_ns = int(max(config.latency_ms, LATENCY_FLOOR_MS) * 1_000_000)
    exec_ts = intent.ts_ns + latency_ns
    slip = config.slippage_bps / 10_000.0

    if intent.side == "buy":
        base = ask_px if ask_px > 0 else (intent.limit_price or 0.0)
        if base <= 0:
            return None
        price = base * (1.0 + slip)
        fees = config.fee_per_share * intent.qty
        return TradeFill(exec_ts, "buy", price, intent.qty, fees, price - base)
    if intent.side == "sell":
        base = bid_px if bid_px > 0 else (intent.limit_price or 0.0)
        if base <= 0:
            return None
        price = base * (1.0 - slip)
        fees = config.fee_per_share * intent.qty
        if intent.side == "sell" and config.allow_short:
            fees += config.borrow_fee_bps / 10_000.0 * price * intent.qty
        return TradeFill(exec_ts, "sell", price, intent.qty, fees, base - price)
    return None
