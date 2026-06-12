"""Phase 19 inert execution-boundary configuration."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from trade_manager.order_intent import TradeManagerOrderIntent
from trade_manager.order_state import TradeManagerOrderState, TradeManagerOrderTransition
from trade_manager.risk_layer import TradeManagerRiskDecision


EXECUTION_MODES = frozenset({"REPLAY", "PAPER", "LIVE"})
EXECUTION_ADAPTERS = frozenset({"hftbacktest_simulated_exchange", "paper_broker", "live_broker"})
ORDER_ROUTING_MODES = frozenset({"direct", "aggregated"})
RECONNECT_HANDLING_MODES = frozenset({"automatic", "manual"})


class TradeManagerExecutionBoundaryError(ValueError):
    """Raised when Phase 19 execution-boundary config is invalid."""

    def __init__(self, reason: str, *, invalid_fields: list[str] | None = None) -> None:
        self.reason = reason
        self.invalid_fields = invalid_fields or []
        detail = f": {reason}"
        if self.invalid_fields:
            detail += f"; invalid_fields={self.invalid_fields}"
        super().__init__(f"Trade Manager execution boundary failed{detail}")


@dataclass(frozen=True)
class TradeManagerExecutionConfig:
    mode: str = "REPLAY"
    adapter: str = "hftbacktest_simulated_exchange"
    live_broker: str = ""
    venue: str = "CME"
    order_routing: str = "direct"
    reconnect_handling: str = "manual"
    heartbeat_interval_sec: int = 30
    route_enabled: bool = False
    host_role: str = "dev_workstation"

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TradeManagerExecutionConfig":
        if not isinstance(raw, dict):
            raise TradeManagerExecutionBoundaryError("EXECUTION_CONFIG_NOT_OBJECT")
        values: dict[str, Any] = {}
        field_names = {field.name for field in fields(cls)}
        unknown_fields = sorted(str(key) for key in raw if key not in field_names)
        if unknown_fields:
            raise TradeManagerExecutionBoundaryError("EXECUTION_CONFIG_UNKNOWN_FIELDS", invalid_fields=unknown_fields)
        for name in field_names:
            if name in raw:
                values[name] = raw[name]
        return cls(**values).normalized()

    def validate(self) -> None:
        invalid: list[str] = []
        mode = self.mode.upper() if isinstance(self.mode, str) else ""
        adapter = self.adapter
        if not isinstance(self.mode, str):
            invalid.append("mode")
        if not isinstance(adapter, str):
            invalid.append("adapter")
        if not isinstance(self.live_broker, str):
            invalid.append("live_broker")
        if not isinstance(self.venue, str):
            invalid.append("venue")
        if not isinstance(self.order_routing, str):
            invalid.append("order_routing")
        if not isinstance(self.reconnect_handling, str):
            invalid.append("reconnect_handling")
        if not isinstance(self.route_enabled, bool):
            invalid.append("route_enabled")
        if not isinstance(self.host_role, str):
            invalid.append("host_role")
        if mode not in EXECUTION_MODES:
            invalid.append("mode")
        if isinstance(adapter, str) and adapter not in EXECUTION_ADAPTERS:
            invalid.append("adapter")
        if isinstance(self.order_routing, str) and self.order_routing not in ORDER_ROUTING_MODES:
            invalid.append("order_routing")
        if isinstance(self.reconnect_handling, str) and self.reconnect_handling not in RECONNECT_HANDLING_MODES:
            invalid.append("reconnect_handling")
        if (
            not isinstance(self.heartbeat_interval_sec, int)
            or isinstance(self.heartbeat_interval_sec, bool)
            or self.heartbeat_interval_sec <= 0
        ):
            invalid.append("heartbeat_interval_sec")
        expected_adapters = {
            "REPLAY": {"hftbacktest_simulated_exchange"},
            "PAPER": {"paper_broker"},
            "LIVE": {"live_broker"},
        }.get(mode)
        if expected_adapters is not None and adapter not in expected_adapters:
            invalid.append("adapter")
        if mode == "LIVE" and self.live_broker != "rithmic":
            invalid.append("live_broker")
        if mode != "LIVE" and self.live_broker:
            invalid.append("live_broker")
        if self.route_enabled:
            invalid.append("route_enabled")
        if invalid:
            reason = "ROUTING_NOT_IMPLEMENTED_IN_PHASE19" if "route_enabled" in invalid else "EXECUTION_CONFIG_INVALID"
            raise TradeManagerExecutionBoundaryError(reason, invalid_fields=list(dict.fromkeys(invalid)))

    def normalized(self) -> "TradeManagerExecutionConfig":
        self.validate()
        return TradeManagerExecutionConfig(
            mode=self.mode.upper(),
            adapter=self.adapter,
            live_broker=self.live_broker,
            venue=self.venue,
            order_routing=self.order_routing,
            reconnect_handling=self.reconnect_handling,
            heartbeat_interval_sec=self.heartbeat_interval_sec,
            route_enabled=self.route_enabled,
            host_role=self.host_role,
        )

    def to_dict(self) -> dict[str, Any]:
        config = self.normalized()
        return {
            "mode": config.mode,
            "adapter": config.adapter,
            "live_broker": config.live_broker,
            "venue": config.venue,
            "order_routing": config.order_routing,
            "reconnect_handling": config.reconnect_handling,
            "heartbeat_interval_sec": config.heartbeat_interval_sec,
            "route_enabled": config.route_enabled,
            "host_role": config.host_role,
        }


@dataclass(frozen=True)
class TradeManagerExecutionBoundary:
    order_intent_id: str
    model_id: str
    risk_allowed: bool
    risk_reason: str
    order_state: str
    config: TradeManagerExecutionConfig
    route_enabled: bool
    can_route: bool
    route_block_reason: str
    adapter_created: bool = False
    adapter_instance: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "model_id": self.model_id,
            "risk_allowed": self.risk_allowed,
            "risk_reason": self.risk_reason,
            "order_state": self.order_state,
            "config": self.config.to_dict(),
            "route_enabled": self.route_enabled,
            "can_route": self.can_route,
            "route_block_reason": self.route_block_reason,
            "adapter_created": self.adapter_created,
            "adapter_instance": self.adapter_instance,
        }


def load_execution_config(path: Path | str) -> TradeManagerExecutionConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return TradeManagerExecutionConfig.from_dict(raw).normalized()


def prepare_execution_boundary(
    intent: TradeManagerOrderIntent,
    risk_decision: TradeManagerRiskDecision | None,
    latest_transition: TradeManagerOrderTransition | None,
    config: TradeManagerExecutionConfig,
) -> TradeManagerExecutionBoundary:
    normalized = config.normalized()
    if risk_decision is None:
        risk_allowed = False
        risk_reason = "RISK_DECISION_MISSING"
    else:
        risk_allowed = risk_decision.allowed
        risk_reason = risk_decision.reason
    order_state = latest_transition.state.value if latest_transition is not None else ""
    eligible = risk_allowed and order_state == TradeManagerOrderState.RISK_APPROVED.value
    return TradeManagerExecutionBoundary(
        order_intent_id=intent.order_intent_id,
        model_id=intent.model_id,
        risk_allowed=risk_allowed,
        risk_reason=risk_reason,
        order_state=order_state,
        config=normalized,
        route_enabled=False,
        can_route=False,
        route_block_reason="PHASE19_INERT_BOUNDARY" if eligible else "ORDER_NOT_EXECUTION_ELIGIBLE",
    )
