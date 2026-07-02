"""Authoritative CME execution specs for the money path.

Single source of truth for tick size, tick value, and contract multiplier of
every instrument the HftBacktest-only lane is allowed to price. Unknown
instruments fail closed — a run must never fall back to another product's
economics (the pre-2026-07 pipeline silently priced everything as ES/MES).

Values are exact CME contract arithmetic (tick_value == tick_size *
contract_multiplier), asserted at import time. `apps/workbench/config/
hot_memory_universe.yaml` must agree for shared symbols; a parity test
enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass


class InstrumentSpecError(ValueError):
    """Raised when an instrument's execution spec cannot be resolved."""


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    tick_size: float
    tick_value: float
    contract_multiplier: float


_SPEC_ROWS: tuple[tuple[str, float, float, float], ...] = (
    # symbol, tick_size (points), tick_value ($/tick), contract_multiplier ($/point)
    # --- equity index e-minis ---
    ("ES", 0.25, 12.50, 50.0),
    ("NQ", 0.25, 5.00, 20.0),
    ("RTY", 0.10, 5.00, 50.0),
    ("YM", 1.00, 5.00, 5.0),
    # --- equity index micros ---
    ("MES", 0.25, 1.25, 5.0),
    ("MNQ", 0.25, 0.50, 2.0),
    ("M2K", 0.10, 0.50, 5.0),
    ("MYM", 1.00, 0.50, 0.5),
    # --- CBOT treasuries (points quoted in 32nds and fractions thereof) ---
    ("ZT", 0.00390625, 7.8125, 2000.0),
    ("ZF", 0.0078125, 7.8125, 1000.0),
    ("ZN", 0.015625, 15.625, 1000.0),
    ("ZB", 0.03125, 31.25, 1000.0),
    ("UB", 0.03125, 31.25, 1000.0),
    # --- STIR ---
    ("SR3", 0.0025, 6.25, 2500.0),
    ("ZQ", 0.0025, 10.4175, 4167.0),
)

INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    symbol: InstrumentSpec(symbol, tick_size, tick_value, multiplier)
    for symbol, tick_size, tick_value, multiplier in _SPEC_ROWS
}

for _spec in INSTRUMENT_SPECS.values():
    assert abs(_spec.tick_size * _spec.contract_multiplier - _spec.tick_value) < 1e-9, (
        f"inconsistent spec for {_spec.symbol}"
    )


def normalize_product(symbol: str) -> str:
    """`MES.v.0` / `MES.c.0` / `MESH6`-style research symbols -> product code."""
    product = str(symbol or "").split(".")[0].strip().upper()
    if not product:
        raise InstrumentSpecError("instrument_spec_missing:empty_symbol")
    return product


def resolve_instrument_spec(symbol: str) -> InstrumentSpec:
    product = normalize_product(symbol)
    spec = INSTRUMENT_SPECS.get(product)
    if spec is None:
        raise InstrumentSpecError(f"instrument_spec_missing:{product}")
    return spec
