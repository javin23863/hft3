"""EquitySessionContext — catalog key for an equity backtest session."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EquitySessionContext:
    underlying_symbol: str
    session_date: str
    decision_timestamp_ns: int
    equity_data_source: str
    equity_schema_used: str
    equity_npz_path: str
    equity_normalized_path: str
    float_metadata_path: str
    catalog_yaml_path: str
    l3_only: bool = True
    notes: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.underlying_symbol:
            raise ValueError("underlying_symbol required")
        if not self.session_date:
            raise ValueError("session_date required")
        if self.decision_timestamp_ns <= 0:
            raise ValueError("decision_timestamp_ns must be positive")
        if self.equity_schema_used != "mbo":
            raise ValueError(
                f"equity_schema_used must be mbo (L3-only); got {self.equity_schema_used!r}"
            )
        if not Path(self.equity_normalized_path).exists():
            raise ValueError(f"equity_normalized_path missing: {self.equity_normalized_path}")
        if not Path(self.equity_npz_path).exists():
            raise ValueError(f"equity_npz_path missing: {self.equity_npz_path}")
        if not self.l3_only:
            raise ValueError("l3_only must be True for operational research runs")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d
