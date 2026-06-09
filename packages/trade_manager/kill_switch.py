"""Phase 21 inert Trade Manager kill-switch decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

import yaml

from trade_manager.monitor import (
    POSITION_STATUS_MISMATCH,
    POSITION_STATUS_OK,
    POSITION_STATUS_UNKNOWN,
    PositionReconciliationResult,
)


KILL_SWITCH_TRIGGERS = (
    "max_daily_loss_breach",
    "max_drawdown_breach",
    "position_limit_breach",
    "runaway_order_rate",
    "runaway_cancel_rate",
    "stale_market_data",
    "broker_disconnect",
    "execution_adapter_failure",
    "position_mismatch",
    "fill_reconciliation_failure",
    "abnormal_slippage",
    "abnormal_latency",
)

KILL_SWITCH_ACTIONS = (
    "stop_new_orders",
    "cancel_open_orders",
    "flatten_positions_if_configured",
    "disable_affected_model",
    "log_event",
    "create_incident_report",
    "update_observer_state",
)


class KillSwitchConfigError(ValueError):
    """Raised when Phase 21 kill-switch config is invalid."""

    def __init__(self, reason: str, *, invalid_fields: list[str] | None = None) -> None:
        self.reason = reason
        self.invalid_fields = invalid_fields or []
        detail = f": {reason}"
        if self.invalid_fields:
            detail += f"; invalid_fields={self.invalid_fields}"
        super().__init__(f"Trade Manager kill switch config failed{detail}")


@dataclass(frozen=True)
class KillSwitchThresholds:
    max_daily_loss: float = 1000.0
    max_drawdown: float = 1000.0
    max_position_abs: float = 3.0
    max_order_rate: float = 10.0
    max_cancel_rate: float = 20.0
    stale_market_data_max_ns: int = 5_000_000
    max_slippage_ticks: float = 8.0
    max_latency_ns: int = 50_000_000

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "KillSwitchThresholds":
        if not isinstance(raw, Mapping):
            raise KillSwitchConfigError("KILL_SWITCH_THRESHOLDS_NOT_OBJECT")
        field_names = {field.name for field in fields(cls)}
        unknown_fields = sorted(str(key) for key in raw if key not in field_names)
        if unknown_fields:
            raise KillSwitchConfigError("KILL_SWITCH_THRESHOLDS_UNKNOWN_FIELDS", invalid_fields=unknown_fields)
        return cls(**{name: raw[name] for name in field_names if name in raw})

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise KillSwitchConfigError(f"{name} must be finite", invalid_fields=[name])
            if value < 0:
                raise KillSwitchConfigError("KILL_SWITCH_THRESHOLD_NEGATIVE", invalid_fields=[name])
        _validate_config_timestamp_ns("stale_market_data_max_ns", self.stale_market_data_max_ns)
        _validate_config_timestamp_ns("max_latency_ns", self.max_latency_ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_daily_loss": self.max_daily_loss,
            "max_drawdown": self.max_drawdown,
            "max_position_abs": self.max_position_abs,
            "max_order_rate": self.max_order_rate,
            "max_cancel_rate": self.max_cancel_rate,
            "stale_market_data_max_ns": self.stale_market_data_max_ns,
            "max_slippage_ticks": self.max_slippage_ticks,
            "max_latency_ns": self.max_latency_ns,
        }


@dataclass(frozen=True)
class KillSwitchConfig:
    thresholds: KillSwitchThresholds = field(default_factory=KillSwitchThresholds)
    trigger_actions: Mapping[str, tuple[str, ...]] | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "KillSwitchConfig":
        if not isinstance(raw, Mapping):
            raise KillSwitchConfigError("KILL_SWITCH_CONFIG_NOT_OBJECT")
        field_names = {"thresholds", "trigger_actions"}
        unknown_fields = sorted(str(key) for key in raw if key not in field_names)
        if unknown_fields:
            raise KillSwitchConfigError("KILL_SWITCH_CONFIG_UNKNOWN_FIELDS", invalid_fields=unknown_fields)
        thresholds = KillSwitchThresholds.from_dict(raw.get("thresholds", {}))
        trigger_actions = _normalize_trigger_actions(raw.get("trigger_actions", {}))
        return cls(thresholds=thresholds, trigger_actions=trigger_actions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger_actions", _normalize_trigger_actions(self.trigger_actions or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "thresholds": self.thresholds.to_dict(),
            "trigger_actions": {trigger: list(actions) for trigger, actions in self.trigger_actions.items()},
        }


@dataclass(frozen=True)
class KillSwitchContext:
    timestamp_ns: int
    daily_loss: float = 0.0
    current_drawdown: float = 0.0
    position_abs: float = 0.0
    order_rate: float = 0.0
    cancel_rate: float = 0.0
    last_market_data_ns: int | None = None
    duplicate_market_data_count: int = 0
    broker_connected: bool = True
    execution_adapter_ok: bool = True
    position_reconciliation: PositionReconciliationResult | None = None
    fill_reconciliation_ok: bool = True
    slippage_ticks: float = 0.0
    latency_ns: int = 0

    def __post_init__(self) -> None:
        _validate_timestamp_ns("timestamp_ns", self.timestamp_ns)
        for name in ("daily_loss", "current_drawdown", "position_abs", "order_rate", "cancel_rate", "slippage_ticks"):
            value = getattr(self, name)
            _validate_finite_number(name, value)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        _validate_timestamp_ns("latency_ns", self.latency_ns)
        if self.last_market_data_ns is not None:
            _validate_timestamp_ns("last_market_data_ns", self.last_market_data_ns)
        if not isinstance(self.duplicate_market_data_count, int) or isinstance(self.duplicate_market_data_count, bool):
            raise ValueError("duplicate_market_data_count must be a non-negative integer")
        if self.duplicate_market_data_count < 0:
            raise ValueError("duplicate_market_data_count must be non-negative")
        for name in ("broker_connected", "execution_adapter_ok", "fill_reconciliation_ok"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if self.position_reconciliation is not None and not isinstance(
            self.position_reconciliation, PositionReconciliationResult
        ):
            raise ValueError("position_reconciliation must be a PositionReconciliationResult")


@dataclass(frozen=True)
class KillSwitchEvent:
    timestamp_ns: int
    trigger: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_timestamp_ns("timestamp_ns", self.timestamp_ns)
        if self.trigger not in KILL_SWITCH_TRIGGERS:
            raise ValueError("trigger must be a documented Phase 21 trigger")
        _validate_json_safe("details", self.details)

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp_ns": self.timestamp_ns, "trigger": self.trigger, "details": dict(self.details)}


@dataclass(frozen=True)
class KillSwitchDecision:
    timestamp_ns: int
    active: bool
    triggers: tuple[KillSwitchEvent, ...]
    requested_actions: tuple[str, ...]
    details: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_timestamp_ns("timestamp_ns", self.timestamp_ns)
        if not isinstance(self.active, bool):
            raise ValueError("active must be a boolean")
        for event in self.triggers:
            if not isinstance(event, KillSwitchEvent):
                raise ValueError("triggers must contain KillSwitchEvent values")
        for action in self.requested_actions:
            if action not in KILL_SWITCH_ACTIONS:
                raise ValueError("requested_actions must be documented Phase 21 actions")
        _validate_json_safe("details", self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "active": self.active,
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "requested_actions": list(self.requested_actions),
            "details": dict(self.details),
        }


def load_kill_switch_config(path: Path | str) -> KillSwitchConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return KillSwitchConfig.from_dict(raw)


def evaluate_kill_switch(context: KillSwitchContext, config: KillSwitchConfig) -> KillSwitchDecision:
    events = _collect_trigger_events(context, config.thresholds)
    actions = _requested_actions(events, config)
    return KillSwitchDecision(
        timestamp_ns=context.timestamp_ns,
        active=bool(events),
        triggers=tuple(events),
        requested_actions=actions,
        details={"decision_only": True, "adapter_created": False, "routed": False},
    )


def _collect_trigger_events(context: KillSwitchContext, thresholds: KillSwitchThresholds) -> list[KillSwitchEvent]:
    events: list[KillSwitchEvent] = []
    _append_if(events, context.timestamp_ns, "max_daily_loss_breach", context.daily_loss > thresholds.max_daily_loss, {"daily_loss": context.daily_loss, "max_daily_loss": thresholds.max_daily_loss})
    _append_if(events, context.timestamp_ns, "max_drawdown_breach", context.current_drawdown > thresholds.max_drawdown, {"current_drawdown": context.current_drawdown, "max_drawdown": thresholds.max_drawdown})
    _append_if(events, context.timestamp_ns, "position_limit_breach", context.position_abs > thresholds.max_position_abs, {"position_abs": context.position_abs, "max_position_abs": thresholds.max_position_abs})
    _append_if(events, context.timestamp_ns, "runaway_order_rate", context.order_rate > thresholds.max_order_rate, {"order_rate": context.order_rate, "max_order_rate": thresholds.max_order_rate})
    _append_if(events, context.timestamp_ns, "runaway_cancel_rate", context.cancel_rate > thresholds.max_cancel_rate, {"cancel_rate": context.cancel_rate, "max_cancel_rate": thresholds.max_cancel_rate})
    _market_data_event(events, context, thresholds)
    _append_if(events, context.timestamp_ns, "broker_disconnect", not context.broker_connected, {"broker_connected": context.broker_connected})
    _append_if(events, context.timestamp_ns, "execution_adapter_failure", not context.execution_adapter_ok, {"execution_adapter_ok": context.execution_adapter_ok})
    _position_reconciliation_event(events, context)
    _append_if(events, context.timestamp_ns, "fill_reconciliation_failure", not context.fill_reconciliation_ok, {"fill_reconciliation_ok": context.fill_reconciliation_ok})
    _append_if(events, context.timestamp_ns, "abnormal_slippage", context.slippage_ticks > thresholds.max_slippage_ticks, {"slippage_ticks": context.slippage_ticks, "max_slippage_ticks": thresholds.max_slippage_ticks})
    _append_if(events, context.timestamp_ns, "abnormal_latency", context.latency_ns > thresholds.max_latency_ns, {"latency_ns": context.latency_ns, "max_latency_ns": thresholds.max_latency_ns})
    return events


def _market_data_event(events: list[KillSwitchEvent], context: KillSwitchContext, thresholds: KillSwitchThresholds) -> None:
    if context.last_market_data_ns is None:
        events.append(KillSwitchEvent(context.timestamp_ns, "stale_market_data", {"reason": "MARKET_DATA_TIMESTAMP_MISSING"}))
        return
    if context.last_market_data_ns > context.timestamp_ns:
        events.append(KillSwitchEvent(context.timestamp_ns, "stale_market_data", {"reason": "MARKET_DATA_TIMESTAMP_FROM_FUTURE", "last_market_data_ns": context.last_market_data_ns}))
        return
    if context.duplicate_market_data_count > 0:
        events.append(KillSwitchEvent(context.timestamp_ns, "stale_market_data", {"reason": "MARKET_DATA_DUPLICATE", "duplicate_market_data_count": context.duplicate_market_data_count}))
        return
    age_ns = context.timestamp_ns - context.last_market_data_ns
    _append_if(events, context.timestamp_ns, "stale_market_data", age_ns > thresholds.stale_market_data_max_ns, {"age_ns": age_ns, "stale_market_data_max_ns": thresholds.stale_market_data_max_ns})


def _position_reconciliation_event(events: list[KillSwitchEvent], context: KillSwitchContext) -> None:
    result = context.position_reconciliation
    if result is None or result.status == POSITION_STATUS_OK:
        return
    details = {"status": result.status, "mismatches": list(result.mismatches), "max_abs_mismatch": result.max_abs_mismatch}
    if result.status == POSITION_STATUS_UNKNOWN:
        details["fail_closed"] = True
    elif result.status != POSITION_STATUS_MISMATCH:
        details["fail_closed"] = True
        details["reason"] = "POSITION_RECONCILIATION_STATUS_INVALID"
    events.append(KillSwitchEvent(context.timestamp_ns, "position_mismatch", details))


def _append_if(events: list[KillSwitchEvent], timestamp_ns: int, trigger: str, active: bool, details: dict[str, Any]) -> None:
    if active:
        events.append(KillSwitchEvent(timestamp_ns, trigger, details))


def _requested_actions(events: list[KillSwitchEvent], config: KillSwitchConfig) -> tuple[str, ...]:
    requested: list[str] = []
    for event in events:
        for action in config.trigger_actions.get(event.trigger, ("stop_new_orders", "log_event", "update_observer_state")):
            if action not in requested:
                requested.append(action)
    return tuple(requested)


def _normalize_trigger_actions(raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise KillSwitchConfigError("KILL_SWITCH_TRIGGER_ACTIONS_NOT_OBJECT")
    invalid_fields: list[str] = []
    normalized: dict[str, tuple[str, ...]] = {}
    for trigger, actions in raw.items():
        trigger_name = str(trigger)
        if trigger_name not in KILL_SWITCH_TRIGGERS:
            invalid_fields.append(trigger_name)
            continue
        if not isinstance(actions, (list, tuple)):
            invalid_fields.append(trigger_name)
            continue
        action_names = tuple(str(action) for action in actions)
        if not action_names:
            invalid_fields.append(trigger_name)
            continue
        unknown_actions = [action for action in action_names if action not in KILL_SWITCH_ACTIONS]
        if unknown_actions:
            invalid_fields.extend(unknown_actions)
            continue
        normalized[trigger_name] = action_names
    if invalid_fields:
        raise KillSwitchConfigError("KILL_SWITCH_TRIGGER_ACTIONS_INVALID", invalid_fields=sorted(set(invalid_fields)))
    return normalized


def _validate_timestamp_ns(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_config_timestamp_ns(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise KillSwitchConfigError(f"{name} must be a non-negative integer", invalid_fields=[name])


def _validate_finite_number(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _validate_json_safe(name: str, value: Any) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be JSON-safe and finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} keys must be strings")
            _validate_json_safe(f"{name}.{key}", item)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_safe(f"{name}[{index}]", item)
        return
    raise ValueError(f"{name} must be JSON-safe")
