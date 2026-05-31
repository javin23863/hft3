"""PDF_MODEL_6 — Dow/YM price-weighted index (consumes Model 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .base import BaseStructuralModel, ModelOutput
from .types import BookPressureOutput, DowYMIndexOutput


def price_weighted_index(prices: List[float], divisor: float) -> float:
    """Index = sum(P_i) / Divisor."""
    if divisor <= 0:
        return 0.0
    return sum(prices) / divisor


def component_weights(prices: Dict[str, float]) -> Dict[str, float]:
    total = sum(prices.values())
    if total <= 0:
        return {k: 0.0 for k in prices}
    return {k: v / total for k, v in prices.items()}


class DowYMIndexModel(BaseStructuralModel[DowYMIndexOutput]):
    model_id = "DOW_YM_INDEX"

    def __init__(self, params: Optional[dict] = None):
        self._params = params or {}
        self._config = self._load_constituents()

    def _load_constituents(self) -> dict:
        path = Path(__file__).resolve().parents[2] / "config" / "dow_constituents.yaml"
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def evaluate(self, **kwargs: Any) -> ModelOutput[DowYMIndexOutput]:
        cfg = kwargs.get("constituents_config", self._config)
        divisor = float(kwargs.get("divisor", cfg.get("divisor", 1.0)))
        constituents: List[dict] = kwargs.get("constituents", cfg.get("constituents", []))

        prices: Dict[str, float] = {}
        for c in constituents:
            prices[str(c["symbol"])] = float(c["price"])

        weights = component_weights(prices)
        ranked = sorted(prices.items(), key=lambda x: x[1], reverse=True)
        top_n = int(kwargs.get("top_n", 5))

        book_by_asset: Dict[str, BookPressureOutput] = kwargs.get("book_pressure_by_asset", {})
        top_ofi: Dict[str, float] = {}
        pressure_sum = 0.0
        weight_sum = 0.0

        for sym, px in ranked[:top_n]:
            w = weights.get(sym, 0.0)
            ofi = 0.0
            if sym in book_by_asset:
                ofi = book_by_asset[sym].OFI_smooth
            elif sym in kwargs.get("ofi_by_symbol", {}):
                ofi = float(kwargs["ofi_by_symbol"][sym])
            top_ofi[sym] = ofi
            pressure_sum += w * ofi
            weight_sum += w

        index_level = price_weighted_index(list(prices.values()), divisor)
        synthetic_pressure = pressure_sum / weight_sum if weight_sum > 0 else 0.0
        ym_signal = synthetic_pressure * (index_level / (index_level + 1e-9))

        payload = DowYMIndexOutput(
            component_price_weight=weights,
            top_component_OFI=top_ofi,
            synthetic_Dow_pressure=synthetic_pressure,
            YM_fair_pressure=synthetic_pressure,
            constituent_to_YM_signal=ym_signal,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=kwargs.get("timestamp_ns", 0),
            payload=payload,
            metadata={"index_level": index_level},
        )
