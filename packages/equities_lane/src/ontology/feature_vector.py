"""StockOptionFeatureVector — combined equity + options features at a decision ts."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class StockOptionFeatureVector:
    underlying_symbol: str
    decision_timestamp_ns: int
    equity_features: dict[str, float]
    option_features: dict[str, float]
    combined_features: dict[str, float]
    equity_data_source: str
    option_data_source: str
    equity_schema_used: str
    option_schema_used: str
    is_pit_clean: bool

    def validate(self) -> None:
        if not self.is_pit_clean:
            raise ValueError("StockOptionFeatureVector is not point-in-time clean")
        for k, v in self.equity_features.items():
            if not isinstance(v, (int, float)):
                raise ValueError(f"equity feature {k!r} not numeric")
        for k, v in self.option_features.items():
            if not isinstance(v, (int, float)):
                raise ValueError(f"option feature {k!r} not numeric")
        for k, v in self.combined_features.items():
            if not isinstance(v, (int, float)):
                raise ValueError(f"combined feature {k!r} not numeric")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
