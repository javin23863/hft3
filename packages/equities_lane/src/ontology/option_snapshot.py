"""Option chain snapshot and contract-at-decision ontology objects."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class OptionContractAtDecision:
    contract_symbol: str
    underlying: str
    strike: float
    right: str  # 'C' or 'P'
    expiry: str  # YYYY-MM-DD
    listed_at_ts_ns: int
    bid: float
    ask: float
    mid: float
    iv: float
    delta: float
    gamma: float
    dte_days: int

    def validate(self) -> None:
        if self.right not in ("C", "P"):
            raise ValueError(f"right must be C or P, got {self.right!r}")
        if self.strike <= 0:
            raise ValueError(f"strike must be > 0, got {self.strike}")
        if self.listed_at_ts_ns <= 0:
            raise ValueError("listed_at_ts_ns must be positive")
        if self.bid < 0 or self.ask < 0:
            raise ValueError("bid/ask must be non-negative")
        if self.ask < self.bid:
            raise ValueError(f"ask ({self.ask}) < bid ({self.bid})")
        if self.mid <= 0 and (self.bid > 0 or self.ask > 0):
            raise ValueError("mid must be positive when bid or ask is set")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptionChainSnapshotAtDecision:
    decision_timestamp_ns: int
    spot: float
    iv_atm: float
    iv_term_atm: float
    iv_skew_25d: float
    gex_net: float
    dex_net: float
    call_wall_strike: float
    put_wall_strike: float
    pc_ratio_volume: float
    num_quotes: int
    coverage: float
    contracts: tuple[OptionContractAtDecision, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if self.decision_timestamp_ns <= 0:
            raise ValueError("decision_timestamp_ns must be positive")
        if self.spot < 0:
            raise ValueError(f"spot must be >= 0, got {self.spot}")
        for c in self.contracts:
            if c.listed_at_ts_ns > self.decision_timestamp_ns:
                raise ValueError(
                    f"contract {c.contract_symbol!r} listed at {c.listed_at_ts_ns} "
                    f"> decision_ts {self.decision_timestamp_ns} (future leakage)"
                )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contracts"] = [c.to_dict() for c in self.contracts]
        return d
