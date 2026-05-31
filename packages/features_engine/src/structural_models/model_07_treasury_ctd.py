"""PDF_MODEL_7 — Treasury CTD, delivery cost, implied repo."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .base import BaseStructuralModel, ModelOutput
from .types import TreasuryCTDOutput


def delivery_cost(
    futures_price: float,
    bond_price: float,
    conversion_factor: float,
) -> float:
    """Cost of delivery: bond price - futures * CF."""
    return bond_price - futures_price * conversion_factor


def implied_repo_rate(
    futures_price: float,
    bond_price: float,
    conversion_factor: float,
    days_to_delivery: float,
) -> float:
    """Implied repo from basis: (F*CF - P) / P * (360/days)."""
    if bond_price <= 0 or days_to_delivery <= 0:
        return 0.0
    basis = futures_price * conversion_factor - bond_price
    return (basis / bond_price) * (360.0 / days_to_delivery)


def select_ctd(costs: Dict[str, float]) -> str:
    if not costs:
        return ""
    return min(costs, key=costs.get)


def ctd_switch_threshold(costs: Dict[str, float], current_ctd: str) -> float:
    """Margin between cheapest and second-cheapest delivery cost."""
    if len(costs) < 2:
        return 0.0
    sorted_costs = sorted(costs.values())
    return sorted_costs[1] - sorted_costs[0]


class TreasuryCTDModel(BaseStructuralModel[TreasuryCTDOutput]):
    model_id = "TREASURY_CTD"

    def __init__(self, params: Optional[dict] = None):
        self._params = params or {}
        self._config = self._load_basket()

    def _load_basket(self) -> dict:
        path = Path(__file__).resolve().parents[2] / "config" / "treasury_deliverable_basket.yaml"
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def evaluate(self, **kwargs: Any) -> ModelOutput[TreasuryCTDOutput]:
        cfg = kwargs.get("basket_config", self._config)
        futures_price = float(kwargs.get("futures_price", cfg.get("futures_price", 0.0)))
        bonds: List[dict] = kwargs.get("bonds", cfg.get("bonds", []))
        days = float(kwargs.get("days_to_delivery", 90.0))

        costs: Dict[str, float] = {}
        repos: Dict[str, float] = {}
        for b in bonds:
            cusip = str(b.get("cusip", b.get("id", "")))
            price = float(b["price"])
            cf = float(b["conversion_factor"])
            costs[cusip] = delivery_cost(futures_price, price, cf)
            repos[cusip] = implied_repo_rate(futures_price, price, cf, days)

        ctd = select_ctd(costs)
        switch_thresh = ctd_switch_threshold(costs, ctd)
        if costs:
            min_cost = min(costs.values())
            max_cost = max(costs.values())
            quality_pressure = (max_cost - min_cost) / (abs(min_cost) + 1e-9)
        else:
            quality_pressure = 0.0

        if ctd and ctd in costs:
            basis_signal = -costs[ctd]
        else:
            basis_signal = 0.0

        payload = TreasuryCTDOutput(
            current_CTD=ctd,
            delivery_cost_by_bond=costs,
            implied_repo_by_bond=repos,
            CTD_switch_threshold=switch_thresh,
            quality_option_pressure=quality_pressure,
            futures_basis_signal=basis_signal,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=kwargs.get("timestamp_ns", 0),
            payload=payload,
        )
