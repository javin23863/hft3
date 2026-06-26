"""Execution cost model for the continuous CME lane (Phase 6 §10).

Subtracts spread, exchange fees, slippage, and market-impact costs from gross
PnL to produce net and fill-adjusted PnL. Microstructure-realistic only — no
theoretical option pricing.

References (PDF §10): spread paid, slippage estimate, adverse selection, impact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class CostModelConfig:
    """Per-contract execution cost parameters.

    Units are price-point units (e.g. dollars per contract for fees, ticks for
    spread). ``contract_multiplier`` scales tick-value costs into currency.
    """

    tick_value: float = 1.0
    contract_multiplier: float = 1.0
    exchange_fee_per_lot: float = 0.0
    clearing_fee_per_lot: float = 0.0
    commission_per_lot: float = 0.0
    slippage_ticks: float = 0.0
    spread_ticks: float = 0.0
    impact_coefficient: float = 0.0  # linear impact per lot traded
    adverse_selection_bps: float = 0.0  # basis points of notional per fill


@dataclass
class CostBreakdown:
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
            "gross_pnl": self.gross_pnl,
            "spread_paid": self.spread_paid,
            "fees_paid": self.fees_paid,
            "slippage_cost": self.slippage_cost,
            "impact_cost": self.impact_cost,
            "adverse_selection_cost": self.adverse_selection_cost,
            "net_pnl": self.net_pnl,
            "fill_adjusted_pnl": self.fill_adjusted_pnl,
            "turnover": self.turnover,
            "num_trades": self.num_trades,
        }


def _cost_per_lot(cfg: CostModelConfig) -> float:
    return (
        cfg.exchange_fee_per_lot
        + cfg.clearing_fee_per_lot
        + cfg.commission_per_lot
    ) * cfg.contract_multiplier


def apply_costs(
    gross_pnl: float,
    trades: Iterable[dict],
    cfg: CostModelConfig,
) -> CostBreakdown:
    """Apply the cost model to a list of trade records.

    Each trade dict may carry: ``side`` ("buy"/"sell"), ``qty`` (lots),
    ``notional`` (currency), ``fill_price``. Missing fields default to 1 lot
    at price 1.0. Returns a full CostBreakdown with net and fill-adjusted PnL.
    """
    trade_list = [t for t in trades if isinstance(t, dict)]
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

    total_cost = (
        spread_paid
        + fees_paid
        + slippage_cost
        + impact_cost
        + adverse_selection_cost
    )
    net_pnl = gross_pnl - total_cost
    fill_adjusted_pnl = gross_pnl - (slippage_cost + impact_cost + adverse_selection_cost)

    return CostBreakdown(
        gross_pnl=gross_pnl,
        spread_paid=spread_paid,
        fees_paid=fees_paid,
        slippage_cost=slippage_cost,
        impact_cost=impact_cost,
        adverse_selection_cost=adverse_selection_cost,
        net_pnl=net_pnl,
        fill_adjusted_pnl=fill_adjusted_pnl,
        turnover=turnover,
        num_trades=num_trades,
    )


def cost_adjusted_returns(
    gross_returns: Iterable[float],
    trades: Iterable[dict],
    cfg: CostModelConfig,
) -> np.ndarray:
    """Subtract per-period cost fraction from a gross return stream.

    Allocates total cost across the return stream proportional to absolute
    gross return, returning a per-bar net return series suitable for Sharpe/DSR.
    Falls back to uniform allocation when gross returns are all zero.
    """
    gross = np.asarray(list(gross_returns), dtype=np.float64)
    breakdown = apply_costs(0.0, trades, cfg)
    total_cost = (
        breakdown.spread_paid
        + breakdown.fees_paid
        + breakdown.slippage_cost
        + breakdown.impact_cost
        + breakdown.adverse_selection_cost
    )
    if total_cost <= 0.0 or gross.size == 0:
        return gross
    abs_sum = float(np.sum(np.abs(gross)))
    if abs_sum <= 0.0:
        per_bar = total_cost / gross.size
        return gross - per_bar
    weights = np.abs(gross) / abs_sum
    return gross - weights * total_cost


def micro_standard_cost_config(tick_value: float = 5.0, multiplier: float = 5.0) -> CostModelConfig:
    """CME micro-standard contract (MES/MNQ/MGC/MCL) default cost config."""
    return CostModelConfig(
        tick_value=tick_value,
        contract_multiplier=multiplier,
        exchange_fee_per_lot=0.35,
        clearing_fee_per_lot=0.10,
        commission_per_lot=0.25,
        slippage_ticks=0.25,
        spread_ticks=1.0,
        impact_coefficient=0.000001,
        adverse_selection_bps=0.5,
    )


def standard_contract_cost_config(tick_value: float = 12.5, multiplier: float = 50.0) -> CostModelConfig:
    """CME standard contract (ES/NQ/GC/CL) default cost config."""
    return CostModelConfig(
        tick_value=tick_value,
        contract_multiplier=multiplier,
        exchange_fee_per_lot=1.10,
        clearing_fee_per_lot=0.25,
        commission_per_lot=0.50,
        slippage_ticks=0.5,
        spread_ticks=1.0,
        impact_coefficient=0.00001,
        adverse_selection_bps=0.75,
    )