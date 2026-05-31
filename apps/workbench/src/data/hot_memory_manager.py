"""HOT/WARM/COLD memory residency manager (Phase 2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Set

from workbench.src.data.instrument_registry import (
    InstrumentRecord,
    load_core_protected_symbols,
    load_instrument_registry,
)

NATIVE_HOT_TIERS = frozenset({"HOT_EXECUTABLE", "HOT_SENSOR"})


def _parse_event_ts(event_ts: str) -> datetime:
    normalized = event_ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        raise ValueError(f"event_ts must include timezone (Z or offset): {event_ts!r}")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class PromotionRecord:
    symbol: str
    reason_code: str
    event_ts: str
    triggering_feature: str
    expected_hot_duration_sec: int
    cooldown_until: str


@dataclass
class HotMemoryManager:
    registry: Mapping[str, InstrumentRecord]
    core_protected_symbols: Set[str]
    base_resident: Set[str] = field(default_factory=set)
    promoted_resident: Set[str] = field(default_factory=set)
    promotion_audit: list[PromotionRecord] = field(default_factory=list)
    cooldown_until: dict[str, datetime] = field(default_factory=dict)
    feed_status: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_repo(cls, repo_root: Path) -> HotMemoryManager:
        registry = load_instrument_registry(repo_root)
        core = set(load_core_protected_symbols(repo_root))
        base = {
            sym
            for sym, rec in registry.items()
            if rec.hot_memory_tier in NATIVE_HOT_TIERS
        }
        mgr = cls(registry=registry, core_protected_symbols=core, base_resident=base)
        for sym, rec in registry.items():
            mgr.feed_status[sym] = {
                "data_delay_status": rec.data_delay_status,
                "last_update_age_ms": None,
            }
        return mgr

    def resident_symbols(self) -> Set[str]:
        return set(self.base_resident) | set(self.promoted_resident)

    def promote(
        self,
        symbol: str,
        *,
        reason_code: str,
        event_ts: str,
        triggering_feature: str,
        expected_hot_duration_sec: int,
    ) -> PromotionRecord:
        rec = self.registry.get(symbol)
        if rec is None:
            raise KeyError(f"unknown symbol: {symbol}")
        if rec.hot_memory_tier not in {"WARM", "COLD"}:
            raise ValueError(f"{symbol} is not WARM/COLD; tier={rec.hot_memory_tier}")
        cooldown = self.cooldown_until.get(symbol)
        event_dt = _parse_event_ts(event_ts)
        if cooldown and event_dt < cooldown:
            raise ValueError(f"{symbol} in cooldown until {cooldown.isoformat()}")

        self.promoted_resident.add(symbol)
        cooldown_ts = event_dt.timestamp() + max(expected_hot_duration_sec, 1)
        cooldown_dt = datetime.fromtimestamp(cooldown_ts, tz=timezone.utc)
        self.cooldown_until[symbol] = cooldown_dt
        record = PromotionRecord(
            symbol=symbol,
            reason_code=reason_code,
            event_ts=event_ts,
            triggering_feature=triggering_feature,
            expected_hot_duration_sec=expected_hot_duration_sec,
            cooldown_until=cooldown_dt.isoformat(),
        )
        self.promotion_audit.append(record)
        return record

    def demote(self, symbol: str, *, reason_code: str, event_ts: str, force: bool = False) -> None:
        event_dt = _parse_event_ts(event_ts)
        if symbol in self.core_protected_symbols and symbol in self.base_resident:
            if not force:
                raise ValueError(f"cannot demote core protected symbol {symbol}")
        if symbol in self.base_resident and symbol not in self.promoted_resident:
            if not force:
                raise ValueError(f"cannot demote native HOT symbol {symbol}")
        self.promoted_resident.discard(symbol)
        self.cooldown_until.pop(symbol, None)
        self.promotion_audit.append(
            PromotionRecord(
                symbol=symbol,
                reason_code=reason_code,
                event_ts=event_ts,
                triggering_feature="demote",
                expected_hot_duration_sec=0,
                cooldown_until=event_dt.isoformat(),
            )
        )

    def apply_load_pressure(self, *, event_ts: Optional[str] = None) -> list[str]:
        ts = event_ts or datetime.now(timezone.utc).isoformat()
        event_dt = _parse_event_ts(ts)
        demoted: list[str] = []
        for sym in sorted(self.promoted_resident):
            if sym in self.core_protected_symbols:
                continue
            rec = self.registry[sym]
            if rec.hot_memory_tier in {"WARM", "COLD"}:
                self.promoted_resident.discard(sym)
                self.cooldown_until.pop(sym, None)
                demoted.append(sym)
                self.promotion_audit.append(
                    PromotionRecord(
                        symbol=sym,
                        reason_code="LOAD_PRESSURE",
                        event_ts=ts,
                        triggering_feature="apply_load_pressure",
                        expected_hot_duration_sec=0,
                        cooldown_until=event_dt.isoformat(),
                    )
                )
        return demoted

    def update_feed_status(
        self,
        symbol: str,
        status: str,
        last_update_age_ms: Optional[int],
    ) -> None:
        if symbol not in self.registry:
            raise KeyError(f"unknown symbol: {symbol}")
        self.feed_status[symbol] = {
            "data_delay_status": status,
            "last_update_age_ms": last_update_age_ms,
        }

    def snapshot_telemetry(self) -> dict[str, Any]:
        missing_sensors = [
            sym
            for sym, rec in self.registry.items()
            if rec.hot_memory_tier == "HOT_SENSOR"
            and self.feed_status.get(sym, {}).get("data_delay_status") == "MISSING"
        ]
        warm = [s for s, r in self.registry.items() if r.hot_memory_tier == "WARM"]
        cold = [s for s, r in self.registry.items() if r.hot_memory_tier == "COLD"]
        return {
            "registry_status": "ok",
            "hot_executable": sorted(
                s for s, r in self.registry.items() if r.hot_memory_tier == "HOT_EXECUTABLE"
            ),
            "hot_sensor": sorted(
                s for s, r in self.registry.items() if r.hot_memory_tier == "HOT_SENSOR"
            ),
            "warm": sorted(warm),
            "cold": sorted(cold),
            "resident": sorted(self.resident_symbols()),
            "promoted_resident": sorted(self.promoted_resident),
            "core_protected_symbols": sorted(self.core_protected_symbols),
            "missing_sensor_warnings": missing_sensors,
            "promotion_audit_tail": [asdict(p) for p in self.promotion_audit[-10:]],
            "degradation_flags": {
                "load_pressure_demotions_available": bool(self.promoted_resident),
                "missing_vix_family": any(m in missing_sensors for m in ("VIX", "VVIX")),
            },
            "feed_status": dict(self.feed_status),
        }


def hot_memory_telemetry_snapshot(repo_root: Path) -> dict[str, Any]:
    try:
        return HotMemoryManager.from_repo(repo_root).snapshot_telemetry()
    except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
        return _degraded_telemetry(str(exc))


def _degraded_telemetry(error: str) -> dict[str, Any]:
    return {
        "registry_status": "degraded",
        "error": error,
        "hot_executable": [],
        "hot_sensor": [],
        "warm": [],
        "cold": [],
        "resident": [],
        "promoted_resident": [],
        "core_protected_symbols": [],
        "missing_sensor_warnings": [],
        "promotion_audit_tail": [],
        "degradation_flags": {
            "load_pressure_demotions_available": False,
            "missing_vix_family": False,
        },
        "feed_status": {},
    }
