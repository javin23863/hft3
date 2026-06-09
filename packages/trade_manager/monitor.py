"""Phase 20 inert position monitor helpers."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Iterable, Mapping


POSITION_STATUS_OK = "OK"
POSITION_STATUS_MISMATCH = "MISMATCH"
POSITION_STATUS_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PositionSnapshot:
    timestamp_ns: int
    source: str
    positions: dict[str, float]
    account_state: Any

    def __post_init__(self) -> None:
        _validate_timestamp_ns("timestamp_ns", self.timestamp_ns)
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string")
        for symbol, quantity in self.positions.items():
            _validate_symbol(symbol)
            _validate_finite_number(f"positions[{symbol!r}]", quantity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "source": self.source,
            "positions": dict(self.positions),
            "account_state": _serialize_value(self.account_state),
        }


@dataclass(frozen=True)
class ExpectedPosition:
    symbol: str
    quantity: float
    source_order_intent_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _validate_finite_number("quantity", self.quantity)
        for intent_id in self.source_order_intent_ids:
            if not isinstance(intent_id, str) or not intent_id:
                raise ValueError("source_order_intent_ids must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "source_order_intent_ids": list(self.source_order_intent_ids),
        }


@dataclass(frozen=True)
class PositionReconciliationResult:
    timestamp_ns: int
    status: str
    mismatches: list[dict[str, Any]]
    max_abs_mismatch: float

    def __post_init__(self) -> None:
        _validate_timestamp_ns("timestamp_ns", self.timestamp_ns)
        if self.status not in {POSITION_STATUS_OK, POSITION_STATUS_MISMATCH, POSITION_STATUS_UNKNOWN}:
            raise ValueError("status must be OK, MISMATCH, or UNKNOWN")
        _validate_finite_number("max_abs_mismatch", self.max_abs_mismatch)
        if self.max_abs_mismatch < 0:
            raise ValueError("max_abs_mismatch must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "status": self.status,
            "mismatches": list(self.mismatches),
            "max_abs_mismatch": self.max_abs_mismatch,
        }


@dataclass(frozen=True)
class PositionMonitorConfig:
    max_position_mismatch_contracts: float
    stale_position_max_ns: int

    def __post_init__(self) -> None:
        _validate_finite_number("max_position_mismatch_contracts", self.max_position_mismatch_contracts)
        if self.max_position_mismatch_contracts < 0:
            raise ValueError("max_position_mismatch_contracts must be non-negative")
        if not isinstance(self.stale_position_max_ns, int) or isinstance(self.stale_position_max_ns, bool):
            raise ValueError("stale_position_max_ns must be a non-negative integer")
        if self.stale_position_max_ns < 0:
            raise ValueError("stale_position_max_ns must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_position_mismatch_contracts": self.max_position_mismatch_contracts,
            "stale_position_max_ns": self.stale_position_max_ns,
        }


def capture_position_snapshot(
    *,
    timestamp_ns: int,
    source: str,
    symbols: Iterable[str] = (),
    adapter: Any | None = None,
    positions: Mapping[str, float] | None = None,
    account_state: Any = None,
) -> PositionSnapshot:
    """Capture observed positions without creating adapters or routing orders."""
    if adapter is not None:
        observed = {symbol: float(adapter.get_position(symbol)) for symbol in symbols}
        account_state = adapter.get_account_state()
    else:
        observed = {symbol: float(quantity) for symbol, quantity in (positions or {}).items()}
    return PositionSnapshot(
        timestamp_ns=int(timestamp_ns),
        source=source,
        positions=observed,
        account_state=account_state,
    )


def reconcile_positions(
    *,
    timestamp_ns: int,
    snapshot: PositionSnapshot | None,
    expected_positions: Iterable[ExpectedPosition],
    config: PositionMonitorConfig,
) -> PositionReconciliationResult:
    _validate_timestamp_ns("timestamp_ns", timestamp_ns)
    expected_by_symbol: dict[str, ExpectedPosition] = {}
    duplicate_symbols: set[str] = set()
    for expected in expected_positions:
        if expected.symbol in expected_by_symbol:
            duplicate_symbols.add(expected.symbol)
        else:
            expected_by_symbol[expected.symbol] = expected
    if duplicate_symbols:
        return PositionReconciliationResult(
            timestamp_ns=timestamp_ns,
            status=POSITION_STATUS_UNKNOWN,
            mismatches=[
                {"symbol": symbol, "reason": "EXPECTED_POSITION_DUPLICATE"}
                for symbol in sorted(duplicate_symbols)
            ],
            max_abs_mismatch=0.0,
        )
    if snapshot is None:
        return PositionReconciliationResult(
            timestamp_ns=timestamp_ns,
            status=POSITION_STATUS_UNKNOWN,
            mismatches=[{"reason": "POSITION_SNAPSHOT_MISSING"}],
            max_abs_mismatch=0.0,
        )

    mismatches: list[dict[str, Any]] = []
    max_abs_mismatch = 0.0
    future_by_ns = snapshot.timestamp_ns - timestamp_ns
    unknown = future_by_ns > 0
    if unknown:
        mismatches.append(
            {
                "reason": "POSITION_SNAPSHOT_FROM_FUTURE",
                "snapshot_timestamp_ns": snapshot.timestamp_ns,
                "future_by_ns": future_by_ns,
            }
        )
    else:
        stale_by_ns = timestamp_ns - snapshot.timestamp_ns
        unknown = stale_by_ns > config.stale_position_max_ns
        if unknown:
            mismatches.append(
                {
                    "reason": "POSITION_SNAPSHOT_STALE",
                    "snapshot_timestamp_ns": snapshot.timestamp_ns,
                    "age_ns": stale_by_ns,
                }
            )

    for symbol in sorted(set(snapshot.positions) | set(expected_by_symbol)):
        expected = expected_by_symbol.get(symbol)
        expected_quantity = expected.quantity if expected is not None else 0.0
        if symbol not in snapshot.positions:
            unknown = True
            mismatches.append(
                {
                    "symbol": symbol,
                    "reason": "POSITION_OBSERVATION_MISSING",
                    "expected": expected_quantity,
                    "source_order_intent_ids": list(expected.source_order_intent_ids) if expected is not None else [],
                }
            )
            continue

        observed_quantity = snapshot.positions[symbol]
        abs_mismatch = abs(observed_quantity - expected_quantity)
        max_abs_mismatch = max(max_abs_mismatch, abs_mismatch)
        if abs_mismatch > config.max_position_mismatch_contracts:
            mismatches.append(
                {
                    "symbol": symbol,
                    "reason": "POSITION_MISMATCH",
                    "expected": expected_quantity,
                    "observed": observed_quantity,
                    "abs_mismatch": abs_mismatch,
                    "source_order_intent_ids": list(expected.source_order_intent_ids) if expected is not None else [],
                }
            )

    status = POSITION_STATUS_OK
    if unknown:
        status = POSITION_STATUS_UNKNOWN
    elif mismatches:
        status = POSITION_STATUS_MISMATCH
    return PositionReconciliationResult(
        timestamp_ns=timestamp_ns,
        status=status,
        mismatches=mismatches,
        max_abs_mismatch=max_abs_mismatch,
    )


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol must be a non-empty string")


def _validate_timestamp_ns(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_finite_number(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
