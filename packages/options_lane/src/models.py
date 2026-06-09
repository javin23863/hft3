"""Data models for config-driven put/call parity groups."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RateSpec:
    source: str
    value: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RateSpec:
        return cls(source=str(data.get("source", "constant")), value=float(data.get("value", 0.0)))


@dataclass(frozen=True)
class LegSpec:
    role: str
    symbol: str
    dataset: str = ""
    stype_in: str = "continuous"
    schema: str = ""
    strike: float | None = None
    right: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegSpec:
        strike = data.get("strike")
        return cls(
            role=str(data["role"]),
            symbol=str(data["symbol"]),
            dataset=str(data.get("dataset", "")),
            stype_in=str(data.get("stype_in", "continuous")),
            schema=str(data.get("schema", "")),
            strike=float(strike) if strike is not None else None,
            right=data.get("right"),
        )


@dataclass(frozen=True)
class ParityGroup:
    id: str
    type: str
    rate: RateSpec
    legs: tuple[LegSpec, ...]
    threshold_ticks: float
    tick_size: float
    time_to_expiry_years: float
    basis_model: str | None = None
    basis_params: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParityGroup:
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            rate=RateSpec.from_dict(data.get("rate", {"source": "constant", "value": 0.0})),
            legs=tuple(LegSpec.from_dict(leg) for leg in data.get("legs", [])),
            threshold_ticks=float(data.get("threshold_ticks", 1.0)),
            tick_size=float(data.get("tick_size", 0.25)),
            time_to_expiry_years=float(data.get("time_to_expiry_years", 0.25)),
            basis_model=data.get("basis_model"),
            basis_params={k: float(v) for k, v in data.get("basis_params", {}).items()},
        )


@dataclass(frozen=True)
class LegQuote:
    role: str
    symbol: str
    bid: float
    ask: float
    timestamp_ns: int
    strike: float | None = None
    right: str | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class QuoteSnapshot:
    group_id: str
    timestamp_ns: int
    quotes: dict[str, LegQuote]

    def quote_at_or_before(self, role: str, t_ns: int) -> LegQuote | None:
        q = self.quotes.get(role)
        if q is None or q.timestamp_ns > t_ns:
            return None
        return q


@dataclass
class Violation:
    group_id: str
    timestamp_ns: int
    residual: float
    edge_ticks: float
    edge_after_fees: float
    legs_used: list[str]
    observed_spread: float
    theoretical_spread: float
    underlying_eff: float
