"""Deterministic transaction-cost helpers for edge evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CostBreakdown:
    spread: float
    commission: float
    slippage: float
    impact: float

    @property
    def total(self) -> float:
        return self.spread + self.commission + self.slippage + self.impact


@dataclass(frozen=True)
class CostModel:
    """Simple per-fill cost model.

    ``spread_bps`` is the full quoted spread. A single fill paid against the
    mid incurs half of it; round trips should call ``estimate`` for each fill or
    use quantities that represent total filled units.
    """

    spread_bps: float = 0.0
    commission_per_unit: float = 0.0
    slippage_bps: float = 0.0
    impact_bps: float = 0.0
    impact_coefficient: float = 0.0
    impact_exponent: float = 0.5

    def estimate(self, *, quantity: float, price: float, participation_rate: float = 0.0) -> CostBreakdown:
        qty = abs(float(quantity))
        px = float(price)
        participation = max(0.0, float(participation_rate))
        if not math.isfinite(qty) or not math.isfinite(px) or px < 0.0:
            raise ValueError("quantity and non-negative price must be finite")
        if not math.isfinite(participation):
            raise ValueError("participation_rate must be finite")
        notional = qty * px
        spread = notional * (self.spread_bps / 10_000.0) * 0.5
        commission = qty * self.commission_per_unit
        slippage = notional * (self.slippage_bps / 10_000.0)
        impact_rate = self.impact_bps / 10_000.0
        if self.impact_coefficient:
            impact_rate += self.impact_coefficient * participation**self.impact_exponent
        impact = notional * impact_rate
        return CostBreakdown(spread=spread, commission=commission, slippage=slippage, impact=impact)


def bid_ask_spread_cost(best_bid: float, best_ask: float, quantity: float = 1.0) -> float:
    bid = float(best_bid)
    ask = float(best_ask)
    qty = abs(float(quantity))
    if not all(math.isfinite(value) for value in (bid, ask, qty)) or ask < bid:
        raise ValueError("bid/ask/quantity must be finite and ask must be >= bid")
    return (ask - bid) * 0.5 * qty


def commission_cost(quantity: float = 1.0, commission_per_unit: float = 0.0) -> float:
    qty = abs(float(quantity))
    fee = float(commission_per_unit)
    if not all(math.isfinite(value) for value in (qty, fee)) or fee < 0.0:
        raise ValueError("quantity and commission_per_unit must be finite and non-negative")
    return qty * fee


def slippage_cost(price: float, quantity: float = 1.0, slippage_bps: float = 0.0) -> float:
    px = float(price)
    qty = abs(float(quantity))
    bps = float(slippage_bps)
    if not all(math.isfinite(value) for value in (px, qty, bps)) or px < 0.0 or bps < 0.0:
        raise ValueError("price, quantity, and slippage_bps must be finite and non-negative")
    return px * qty * bps / 10_000.0


def market_impact_cost(
    price: float,
    quantity: float = 1.0,
    *,
    participation_rate: float = 0.0,
    coefficient: float = 0.0,
    exponent: float = 0.5,
) -> float:
    px = float(price)
    qty = abs(float(quantity))
    participation = max(0.0, float(participation_rate))
    coeff = float(coefficient)
    exp = float(exponent)
    if not all(math.isfinite(value) for value in (px, qty, participation, coeff, exp)):
        raise ValueError("impact inputs must be finite")
    if px < 0.0 or coeff < 0.0 or exp <= 0.0:
        raise ValueError("price/coefficient must be non-negative and exponent positive")
    return px * qty * coeff * participation**exp


def _broadcast(values: Sequence[float] | float, n: int, name: str) -> list[float]:
    if isinstance(values, (int, float)):
        out = [float(values)] * n
    else:
        out = [float(v) for v in values]
    if len(out) != n:
        raise ValueError(f"{name} length {len(out)} does not match pnl length {n}")
    if not all(math.isfinite(v) for v in out):
        raise ValueError(f"{name} values must be finite")
    return out


def apply_costs(
    gross_pnl: Sequence[float],
    *,
    quantities: Sequence[float] | float | None = None,
    prices: Sequence[float] | float | None = None,
    model: CostModel | None = None,
    participation_rates: Sequence[float] | float = 0.0,
    config: Mapping[str, Any] | None = None,
    market_data: Mapping[str, Any] | None = None,
) -> list[float] | tuple[list[float], dict[str, float]]:
    """Subtract estimated costs from gross PnL.

    When ``model`` is supplied, returns only the net series. When ``config`` is
    supplied, returns ``(net_series, aggregate_breakdown)`` for compatibility
    with evaluation callers.
    """

    pnl = [float(x) for x in gross_pnl]
    if not all(math.isfinite(x) for x in pnl):
        raise ValueError("gross_pnl values must be finite")
    if model is None:
        cfg = dict(config or {})
        if not cfg:
            return pnl, {"spread": 0.0, "commission": 0.0, "slippage": 0.0, "impact": 0.0, "total": 0.0}
        fixed_spread_cost = float(cfg.get("spread_cost", cfg.get("fixed_spread_cost", 0.0)) or 0.0)
        model = CostModel(
            spread_bps=float(cfg.get("spread_bps", cfg.get("spread", 0.0)) or 0.0),
            commission_per_unit=float(
                cfg.get("commission_per_unit", cfg.get("commission_per_trade", cfg.get("commission", 0.0))) or 0.0
            ),
            slippage_bps=float(cfg.get("slippage_bps", cfg.get("slippage", 0.0)) or 0.0),
            impact_bps=float(cfg.get("impact_bps", cfg.get("impact", 0.0)) or 0.0),
            impact_coefficient=float(cfg.get("impact_coefficient", cfg.get("market_impact_coeff", 0.0)) or 0.0),
            impact_exponent=float(cfg.get("impact_exponent", 0.5) or 0.5),
        )
        quantities = cfg.get("quantities", cfg.get("quantity", cfg.get("contracts_per_trade", 1.0)))
        prices = cfg.get("prices", cfg.get("price", _market_price(market_data) or 1.0))
        participation_rates = cfg.get("participation_rates", cfg.get("participation_rate", participation_rates))
        return_breakdown = True
    else:
        if quantities is None or prices is None:
            raise ValueError("quantities and prices are required when model is supplied")
        fixed_spread_cost = 0.0
        return_breakdown = False
    qty = _broadcast(quantities, len(pnl), "quantities")
    px = _broadcast(prices, len(pnl), "prices")
    participation = _broadcast(participation_rates, len(pnl), "participation_rates")
    net: list[float] = []
    totals = {"spread": 0.0, "commission": 0.0, "slippage": 0.0, "impact": 0.0}
    for pnl_i, q, p, part in zip(pnl, qty, px, participation):
        cost = model.estimate(quantity=q, price=p, participation_rate=part)
        fixed_spread = fixed_spread_cost * abs(q)
        spread = cost.spread + fixed_spread
        totals["spread"] += spread
        totals["commission"] += cost.commission
        totals["slippage"] += cost.slippage
        totals["impact"] += cost.impact
        net.append(pnl_i - cost.total - fixed_spread)
    if return_breakdown:
        totals["total"] = sum(totals.values())
        return net, totals
    return net


def _market_price(market_data: Mapping[str, Any] | None) -> float | None:
    if not isinstance(market_data, Mapping):
        return None
    candidates: list[Any] = []
    for mapping in (market_data, market_data.get("report"), market_data.get("diagnostics")):
        if isinstance(mapping, Mapping):
            candidates.extend(mapping.get(key) for key in ("price", "avg_price", "fill_price", "mid_price"))
    for value in candidates:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price >= 0.0:
            return price
    return None


# ---------------------------------------------------------------------------
# Continuous-lane cost API (PDF section 10). Trade-list driven; produces
# gross/net/fill-adjusted PnL, turnover, spread paid, slippage, impact,
# adverse selection. Distinct names so the scalar edge-eval API above is
# unaffected.
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as _dc_field


@dataclass
class ContinuousCostBreakdown:
    gross_pnl: float
    spread_paid: float
    fees_paid: float
    slippage_cost: float
    impact_cost: float
    adverse_selection_cost: float
    net_pnl: float
    fill_adjusted_pnl: float
    turnover: float
    num_trades: int

    def to_dict(self) -> dict:
        return {
            "gross_pnl": self.gross_pnl, "spread_paid": self.spread_paid,
            "fees_paid": self.fees_paid, "slippage_cost": self.slippage_cost,
            "impact_cost": self.impact_cost, "adverse_selection_cost": self.adverse_selection_cost,
            "net_pnl": self.net_pnl, "fill_adjusted_pnl": self.fill_adjusted_pnl,
            "turnover": self.turnover, "num_trades": self.num_trades,
        }


@dataclass(frozen=True)
class CostModelConfig:
    """Per-contract execution cost parameters for the continuous lane."""

    tick_value: float = 1.0
    contract_multiplier: float = 1.0
    exchange_fee_per_lot: float = 0.0
    clearing_fee_per_lot: float = 0.0
    commission_per_lot: float = 0.0
    slippage_ticks: float = 0.0
    spread_ticks: float = 0.0
    impact_coefficient: float = 0.0
    adverse_selection_bps: float = 0.0


def _cost_per_lot(cfg: CostModelConfig) -> float:
    return (cfg.exchange_fee_per_lot + cfg.clearing_fee_per_lot + cfg.commission_per_lot) * cfg.contract_multiplier


def apply_continuous_costs(
    gross_pnl: float,
    trades: Sequence[Mapping[str, Any]],
    cfg: CostModelConfig,
) -> ContinuousCostBreakdown:
    """Apply the continuous-lane cost model to a list of trade records."""
    trade_list = [t for t in trades if isinstance(t, Mapping)]
    num_trades = len(trade_list)
    turnover = 0.0
    spread_paid = 0.0
    fees_paid = 0.0
    slippage_cost = 0.0
    impact_cost = 0.0
    adverse_selection_cost = 0.0
    per_lot_fee = _cost_per_lot(cfg)
    for t in trade_list:
        qty = float(t.get("qty", 1.0))
        notional = abs(float(t.get("notional", qty * t.get("fill_price", 1.0))))
        turnover += notional
        spread_paid += cfg.spread_ticks * cfg.tick_value * cfg.contract_multiplier * qty
        fees_paid += per_lot_fee * qty
        slippage_cost += cfg.slippage_ticks * cfg.tick_value * cfg.contract_multiplier * qty
        impact_cost += cfg.impact_coefficient * qty * notional
        adverse_selection_cost += notional * (cfg.adverse_selection_bps / 10000.0)
    total_cost = spread_paid + fees_paid + slippage_cost + impact_cost + adverse_selection_cost
    net_pnl = gross_pnl - total_cost
    fill_adjusted_pnl = gross_pnl - (slippage_cost + impact_cost + adverse_selection_cost)
    return ContinuousCostBreakdown(
        gross_pnl=gross_pnl, spread_paid=spread_paid, fees_paid=fees_paid,
        slippage_cost=slippage_cost, impact_cost=impact_cost,
        adverse_selection_cost=adverse_selection_cost, net_pnl=net_pnl,
        fill_adjusted_pnl=fill_adjusted_pnl, turnover=turnover, num_trades=num_trades,
    )


def cost_adjusted_returns(
    gross_returns: Sequence[float],
    trades: Sequence[Mapping[str, Any]],
    cfg: CostModelConfig,
) -> list[float]:
    """Subtract per-period cost fraction from a gross return stream."""
    import numpy as np

    gross = np.asarray(list(gross_returns), dtype=np.float64)
    breakdown = apply_continuous_costs(0.0, list(trades), cfg)
    total_cost = (breakdown.spread_paid + breakdown.fees_paid + breakdown.slippage_cost
                  + breakdown.impact_cost + breakdown.adverse_selection_cost)
    if total_cost <= 0.0 or gross.size == 0:
        return gross.tolist()
    abs_sum = float(np.sum(np.abs(gross)))
    if abs_sum <= 0.0:
        return (gross - (total_cost / gross.size)).tolist()
    weights = np.abs(gross) / abs_sum
    return (gross - weights * total_cost).tolist()


def micro_standard_cost_config(tick_value: float = 5.0, multiplier: float = 5.0) -> CostModelConfig:
    """CME micro-standard contract (MES/MNQ/MGC/MCL) default cost config."""
    return CostModelConfig(
        tick_value=tick_value, contract_multiplier=multiplier,
        exchange_fee_per_lot=0.35, clearing_fee_per_lot=0.10, commission_per_lot=0.25,
        slippage_ticks=0.25, spread_ticks=1.0, impact_coefficient=0.000001,
        adverse_selection_bps=0.5,
    )


def standard_contract_cost_config(tick_value: float = 12.5, multiplier: float = 50.0) -> CostModelConfig:
    """CME standard contract (ES/NQ/GC/CL) default cost config."""
    return CostModelConfig(
        tick_value=tick_value, contract_multiplier=multiplier,
        exchange_fee_per_lot=1.10, clearing_fee_per_lot=0.25, commission_per_lot=0.50,
        slippage_ticks=0.5, spread_ticks=1.0, impact_coefficient=0.00001,
        adverse_selection_bps=0.75,
    )


__all__ = [
    "CostBreakdown",
    "CostModel",
    "apply_costs",
    "bid_ask_spread_cost",
    "commission_cost",
    "market_impact_cost",
    "slippage_cost",
    # Continuous-lane API
    "ContinuousCostBreakdown", "CostModelConfig", "apply_continuous_costs",
    "cost_adjusted_returns", "micro_standard_cost_config", "standard_contract_cost_config",
]
