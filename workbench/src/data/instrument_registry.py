"""Chicago futures instrument registry (hot-memory universe Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml

HOT_MEMORY_TIERS = frozenset({"HOT_EXECUTABLE", "HOT_SENSOR", "WARM", "COLD"})
INSTRUMENT_TYPES = frozenset({"futures", "index_sensor"})
DATA_DELAY_STATUSES = frozenset(
    {"LIVE", "DELAYED", "HISTORICAL_ONLY", "MISSING", "DISABLED"}
)
INDEX_SENSORS = frozenset({"VIX", "VVIX"})


@dataclass(frozen=True)
class InstrumentRecord:
    canonical_internal_symbol: str
    research_symbol: str
    data_vendor_symbol: str
    hot_memory_tier: str
    instrument_type: str
    exchange: str
    venue: str
    asset_class: str
    tradable: bool
    order_book_available: bool
    trade_print_available: bool
    index_sensor_available: bool
    point_in_time_safe: bool
    data_delay_status: str
    live_feed_status: str
    historical_feed_status: str
    broker_symbol: Optional[str] = None
    continuous_contract_symbol: Optional[str] = None
    front_contract_symbol: Optional[str] = None
    next_contract_symbol: Optional[str] = None
    tick_size: float = 0.25
    tick_value: float = 12.5
    contract_multiplier: float = 50.0
    currency: str = "USD"
    min_price_increment: float = 0.25
    roll_rule: str = "volume"
    failure_mode: str = "degrade"
    dependencies: tuple[str, ...] = field(default_factory=tuple)


def _config_path(repo_root: Path) -> Path:
    return repo_root / "workbench" / "config" / "hot_memory_universe.yaml"


def _merge_defaults(raw: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(raw)
    return merged


def _record_from_dict(raw: Mapping[str, Any]) -> InstrumentRecord:
    sym = str(raw["canonical_internal_symbol"])
    deps = raw.get("dependencies") or []
    return InstrumentRecord(
        canonical_internal_symbol=sym,
        research_symbol=str(raw["research_symbol"]),
        data_vendor_symbol=str(raw["data_vendor_symbol"]),
        hot_memory_tier=str(raw["hot_memory_tier"]),
        instrument_type=str(raw.get("instrument_type", "futures")),
        exchange=str(raw["exchange"]),
        venue=str(raw["venue"]),
        asset_class=str(raw.get("asset_class", "futures")),
        tradable=bool(raw.get("tradable", True)),
        order_book_available=bool(raw.get("order_book_available", True)),
        trade_print_available=bool(raw.get("trade_print_available", True)),
        index_sensor_available=bool(raw.get("index_sensor_available", False)),
        point_in_time_safe=bool(raw.get("point_in_time_safe", True)),
        data_delay_status=str(raw.get("data_delay_status", "MISSING")),
        live_feed_status=str(raw.get("live_feed_status", "MISSING")),
        historical_feed_status=str(raw.get("historical_feed_status", "MISSING")),
        broker_symbol=raw.get("broker_symbol"),
        continuous_contract_symbol=raw.get("continuous_contract_symbol"),
        front_contract_symbol=raw.get("front_contract_symbol"),
        next_contract_symbol=raw.get("next_contract_symbol"),
        tick_size=float(raw.get("tick_size", 0.25)),
        tick_value=float(raw.get("tick_value", 12.5)),
        contract_multiplier=float(raw.get("contract_multiplier", 50.0)),
        currency=str(raw.get("currency", "USD")),
        min_price_increment=float(raw.get("min_price_increment", 0.25)),
        roll_rule=str(raw.get("roll_rule", "volume")),
        failure_mode=str(raw.get("failure_mode", "degrade")),
        dependencies=tuple(str(d) for d in deps),
    )


def validate_registry(
    records: Mapping[str, InstrumentRecord],
    *,
    core_protected_symbols: Iterable[str],
) -> None:
    if not records:
        raise ValueError("instrument registry is empty")

    for sym, rec in records.items():
        if sym != rec.canonical_internal_symbol:
            raise ValueError(
                f"registry key {sym!r} != canonical_internal_symbol {rec.canonical_internal_symbol!r}"
            )
        if rec.hot_memory_tier not in HOT_MEMORY_TIERS:
            raise ValueError(f"{sym}: invalid hot_memory_tier {rec.hot_memory_tier!r}")
        if rec.instrument_type not in INSTRUMENT_TYPES:
            raise ValueError(f"{sym}: invalid instrument_type {rec.instrument_type!r}")
        if rec.data_delay_status not in DATA_DELAY_STATUSES:
            raise ValueError(f"{sym}: invalid data_delay_status {rec.data_delay_status!r}")

        if rec.canonical_internal_symbol in INDEX_SENSORS:
            if rec.instrument_type != "index_sensor":
                raise ValueError(f"{sym}: VIX/VVIX must be index_sensor")
            if rec.tradable:
                raise ValueError(f"{sym}: index sensors must not be tradable")
            if rec.order_book_available:
                raise ValueError(f"{sym}: index sensors must not have order book")

        if rec.canonical_internal_symbol in {"VX1", "VX2"}:
            if rec.instrument_type != "futures":
                raise ValueError(f"{sym}: VX legs must be futures")
            if rec.venue != "CFE":
                raise ValueError(f"{sym}: VX legs must use CFE venue")

    for core in core_protected_symbols:
        if core not in records:
            raise ValueError(f"core_protected_symbols missing registry entry: {core}")


def load_instrument_registry(repo_root: Path) -> dict[str, InstrumentRecord]:
    path = _config_path(repo_root)
    if not path.is_file():
        raise FileNotFoundError(f"hot memory universe config missing: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    core = list(raw.get("core_protected_symbols") or [])
    records: dict[str, InstrumentRecord] = {}
    for entry in raw.get("instruments") or []:
        merged = _merge_defaults(entry, defaults)
        rec = _record_from_dict(merged)
        if rec.canonical_internal_symbol in records:
            raise ValueError(f"duplicate canonical_internal_symbol: {rec.canonical_internal_symbol}")
        records[rec.canonical_internal_symbol] = rec
    validate_registry(records, core_protected_symbols=core)
    return records


def load_core_protected_symbols(repo_root: Path) -> list[str]:
    raw = yaml.safe_load(_config_path(repo_root).read_text(encoding="utf-8")) or {}
    return list(raw.get("core_protected_symbols") or [])


def assert_not_executable(symbol: str, records: Mapping[str, InstrumentRecord]) -> None:
    rec = records.get(symbol)
    if rec is None:
        raise KeyError(f"unknown symbol: {symbol}")
    if rec.instrument_type == "index_sensor" or not rec.tradable:
        raise ValueError(f"symbol {symbol} is not executable")


def is_tradable(symbol: str, records: Mapping[str, InstrumentRecord]) -> bool:
    rec = records.get(symbol)
    if rec is None:
        return False
    if rec.instrument_type == "index_sensor" or not rec.tradable:
        return False
    if rec.data_delay_status in {"MISSING", "DISABLED"}:
        return False
    return True
