"""Phase 17 Trade Manager risk layer.

The risk layer evaluates inert Trade Manager order intents and returns decisions.
It does not create execution adapters and never routes, submits, cancels, or
replaces orders.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from execution.interfaces import OrderIntent as ExecutionOrderIntent
from execution.production_safety import (
    HaltAction,
    ProductionSafetyOrchestrator,
    SafetyContext,
    SafetyResult,
)
from trade_manager.order_intent import TradeManagerOrderIntent


@dataclass(frozen=True)
class TradeManagerRiskConfig:
    max_order_size: float = 1.0
    max_position_size: float = 3.0
    max_gross_exposure: float = 3.0
    max_net_exposure: float = 3.0
    max_daily_loss: float = 1000.0
    max_drawdown: float = 1000.0
    max_order_rate: int = 10
    max_cancel_rate: int = 20
    max_open_orders: int = 5
    symbol_eligibility: tuple[str, ...] = ("ES",)
    instrument_eligibility: tuple[str, ...] = ("ES",)
    model_eligibility: tuple[str, ...] = ()
    stale_data_max_ns: int = 5_000_000
    stale_signal_max_ns: int = 50_000_000
    duplicate_order_check: bool = True
    price_band_check: bool = True
    price_band_ticks: float = 8.0
    liquidity_check: bool = False
    spread_check: bool = True
    max_spread_ticks: float = 4.0
    kill_switch_status: str = "armed"
    disconnect_grace_ns: int = 1_000_000_000
    max_clock_drift_ns: int = 50_000_000
    max_position_mismatch_contracts: float = 1.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TradeManagerRiskConfig":
        values: dict[str, Any] = {}
        field_names = {field.name for field in fields(cls)}
        for name in field_names:
            if name in raw:
                values[name] = raw[name]
        for name in ("symbol_eligibility", "instrument_eligibility", "model_eligibility"):
            if name in values:
                values[name] = tuple(str(value) for value in values[name])
        return cls(**values)


@dataclass(frozen=True)
class TradeManagerRiskContext:
    adapter: Any
    execution_mode: str = "EXTERNAL"
    system_clock_ns: int = 0
    exchange_clock_ns: int = 0
    last_market_data_ns: int = 0
    local_inventory: float = 0.0
    local_realized_pnl: float = 0.0
    daily_loss_so_far: float = 0.0
    current_drawdown: float = 0.0
    gross_exposure: float | None = None
    net_exposure: float | None = None
    open_order_count: int = 0
    order_rate: int = 0
    cancel_rate: int = 0
    duplicate_order_intent_ids: tuple[str, ...] = ()
    instrument: str | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    reference_price: float | None = None
    tick_size: float = 0.25
    has_liquidity: bool = True
    last_signal_ns: int = 0


@dataclass(frozen=True)
class TradeManagerRiskDecision:
    allowed: bool
    reason: str
    action: str
    monitor_name: str
    order_intent_id: str
    model_id: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "action": self.action,
            "monitor_name": self.monitor_name,
            "order_intent_id": self.order_intent_id,
            "model_id": self.model_id,
            "details": dict(self.details),
        }


class TradeManagerRiskError(ValueError):
    """Raised when a risk request references invalid Trade Manager state."""

    def __init__(self, model_id: str, reason: str) -> None:
        self.model_id = model_id
        self.reason = reason
        super().__init__(f"Trade Manager risk failed for {model_id!r}: {reason}")


class TradeManagerRiskLayer:
    """Static and production-safety pre-trade risk evaluator."""

    def __init__(
        self,
        config: TradeManagerRiskConfig | None = None,
        *,
        orchestrator: ProductionSafetyOrchestrator | None = None,
    ) -> None:
        self.config = config or TradeManagerRiskConfig()
        self.orchestrator = orchestrator or ProductionSafetyOrchestrator()
        self._configure_orchestrator()

    def _configure_orchestrator(self) -> None:
        self.orchestrator.stale_data.max_stale_ns = self.config.stale_data_max_ns
        self.orchestrator.disconnect.disconnect_grace_ns = self.config.disconnect_grace_ns
        self.orchestrator.clock_drift.max_drift_ns = self.config.max_clock_drift_ns
        self.orchestrator.position_mismatch.max_mismatch = self.config.max_position_mismatch_contracts
        self.orchestrator.daily_loss.loss_limit = self.config.max_daily_loss

    def evaluate(
        self,
        active_model: Any,
        intent: TradeManagerOrderIntent,
        context: TradeManagerRiskContext,
    ) -> TradeManagerRiskDecision:
        self._configure_orchestrator()
        identity_reject = self._identity_reject(active_model, intent, context)
        if identity_reject is not None:
            return identity_reject

        safety_context = SafetyContext(
            order_intent=_execution_intent_from_trade_manager(active_model, intent),
            adapter=context.adapter,
            local_inventory=context.local_inventory,
            local_realized_pnl=context.local_realized_pnl,
            last_market_data_ns=context.last_market_data_ns,
            exchange_clock_ns=context.exchange_clock_ns,
            system_clock_ns=context.system_clock_ns,
            execution_mode=context.execution_mode,
            daily_loss_limit=self.config.max_daily_loss,
            daily_loss_so_far=context.daily_loss_so_far,
        )
        result = self.orchestrator.pre_trade_check(safety_context)
        if not result.allowed:
            return _decision_from_safety_result(intent, result)

        static_reject = self._static_reject(active_model, intent, context)
        if static_reject is not None:
            return static_reject

        return TradeManagerRiskDecision(
            allowed=True,
            reason="RISK_APPROVED",
            action=HaltAction.NONE.name,
            monitor_name="TradeManagerRiskLayer",
            order_intent_id=intent.order_intent_id,
            model_id=intent.model_id,
            details={"production_safety": _safety_result_payload(result)},
        )

    def _identity_reject(
        self,
        active_model: Any,
        intent: TradeManagerOrderIntent,
        context: TradeManagerRiskContext,
    ) -> TradeManagerRiskDecision | None:
        if context.instrument is not None and context.instrument != intent.symbol:
            return _reject(
                intent,
                "INSTRUMENT_SYMBOL_MISMATCH",
                {"instrument": context.instrument, "symbol": intent.symbol},
            )
        active_instruments = tuple(getattr(active_model, "allowed_instruments", ()))
        if active_instruments and intent.symbol not in active_instruments:
            return _reject(
                intent,
                "INSTRUMENT_NOT_ALLOWED_BY_ACTIVE_MODEL",
                {"instrument": intent.symbol, "allowed_instruments": list(active_instruments)},
            )
        return None

    def _static_reject(
        self,
        active_model: Any,
        intent: TradeManagerOrderIntent,
        context: TradeManagerRiskContext,
    ) -> TradeManagerRiskDecision | None:
        cfg = self.config
        if str(cfg.kill_switch_status).lower() != "armed":
            return _reject(intent, "KILL_SWITCH_NOT_ARMED", {"kill_switch_status": cfg.kill_switch_status})
        if cfg.model_eligibility and intent.model_id not in cfg.model_eligibility:
            return _reject(intent, "MODEL_NOT_ELIGIBLE", {"model_eligibility": list(cfg.model_eligibility)})
        if cfg.symbol_eligibility and intent.symbol not in cfg.symbol_eligibility:
            return _reject(intent, "SYMBOL_NOT_ELIGIBLE", {"symbol_eligibility": list(cfg.symbol_eligibility)})
        instrument = intent.symbol
        if cfg.instrument_eligibility and instrument not in cfg.instrument_eligibility:
            return _reject(
                intent,
                "INSTRUMENT_NOT_ELIGIBLE",
                {"instrument": instrument, "instrument_eligibility": list(cfg.instrument_eligibility)},
            )
        projected_position = context.local_inventory + (intent.quantity if intent.side == "BUY" else -intent.quantity)
        pure_reducing_order = (
            context.local_inventory > 0
            and intent.side == "SELL"
            and intent.quantity <= abs(context.local_inventory)
        ) or (
            context.local_inventory < 0
            and intent.side == "BUY"
            and intent.quantity <= abs(context.local_inventory)
        )
        if intent.quantity > cfg.max_order_size and not pure_reducing_order:
            return _reject(intent, "ORDER_SIZE_LIMIT_EXCEEDED", {"quantity": intent.quantity, "max_order_size": cfg.max_order_size})
        if abs(projected_position) > cfg.max_position_size:
            return _reject(intent, "POSITION_SIZE_LIMIT_EXCEEDED", {"projected_position": projected_position})
        if context.open_order_count >= cfg.max_open_orders:
            return _reject(intent, "OPEN_ORDER_LIMIT_EXCEEDED", {"open_order_count": context.open_order_count})
        if context.order_rate >= cfg.max_order_rate:
            return _reject(intent, "ORDER_RATE_LIMIT_EXCEEDED", {"order_rate": context.order_rate})
        if context.cancel_rate >= cfg.max_cancel_rate:
            return _reject(intent, "CANCEL_RATE_LIMIT_EXCEEDED", {"cancel_rate": context.cancel_rate})
        if context.current_drawdown > cfg.max_drawdown:
            return _reject(intent, "DRAWDOWN_LIMIT_EXCEEDED", {"current_drawdown": context.current_drawdown})

        gross_delta = abs(projected_position) - abs(context.local_inventory)
        base_gross = abs(context.local_inventory) if context.gross_exposure is None else context.gross_exposure
        projected_gross = max(0.0, base_gross + gross_delta)
        if projected_gross > cfg.max_gross_exposure:
            return _reject(intent, "GROSS_EXPOSURE_LIMIT_EXCEEDED", {"projected_gross_exposure": projected_gross})
        base_net = context.local_inventory if context.net_exposure is None else context.net_exposure
        projected_net = base_net + (intent.quantity if intent.side == "BUY" else -intent.quantity)
        if abs(projected_net) > cfg.max_net_exposure:
            return _reject(intent, "NET_EXPOSURE_LIMIT_EXCEEDED", {"projected_net_exposure": projected_net})

        if cfg.duplicate_order_check and intent.order_intent_id in context.duplicate_order_intent_ids:
            return _reject(intent, "DUPLICATE_ORDER_INTENT", {"order_intent_id": intent.order_intent_id})
        if context.system_clock_ns and intent.timestamp:
            signal_age_ns = context.system_clock_ns - intent.timestamp
            if signal_age_ns > cfg.stale_signal_max_ns:
                return _reject(intent, "STALE_SIGNAL", {"signal_age_ns": signal_age_ns})
        tick_size = context.tick_size if context.tick_size > 0 else 0.25
        if cfg.price_band_check and context.reference_price is not None and intent.limit_price is not None:
            distance_ticks = abs(float(intent.limit_price) - context.reference_price) / tick_size
            if distance_ticks > cfg.price_band_ticks:
                return _reject(
                    intent,
                    "PRICE_BAND_EXCEEDED",
                    {"distance_ticks": distance_ticks, "price_band_ticks": cfg.price_band_ticks},
                )
        if cfg.liquidity_check and not context.has_liquidity:
            return _reject(intent, "LIQUIDITY_UNAVAILABLE", {"has_liquidity": context.has_liquidity})
        if cfg.spread_check and context.bid_price is not None and context.ask_price is not None:
            if context.ask_price < context.bid_price:
                return _reject(
                    intent,
                    "CROSSED_MARKET",
                    {"bid_price": context.bid_price, "ask_price": context.ask_price},
                )
            spread_ticks = (context.ask_price - context.bid_price) / tick_size
            if spread_ticks > cfg.max_spread_ticks:
                return _reject(
                    intent,
                    "SPREAD_LIMIT_EXCEEDED",
                    {"spread_ticks": spread_ticks, "max_spread_ticks": cfg.max_spread_ticks},
                )
        return None


def load_risk_config(path: Path | str) -> TradeManagerRiskConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TradeManagerRiskError("", "RISK_CONFIG_NOT_OBJECT")
    return TradeManagerRiskConfig.from_dict(raw)


def _execution_intent_from_trade_manager(active_model: Any, intent: TradeManagerOrderIntent) -> ExecutionOrderIntent:
    return ExecutionOrderIntent(
        intent_id=intent.order_intent_id,
        run_id=getattr(active_model, "run_id", ""),
        timestamp_ns=intent.timestamp,
        strategy_id=intent.strategy_id,
        model_id=intent.model_id,
        symbol=intent.symbol,
        side=intent.side,
        order_type=intent.order_type,
        price=float(intent.limit_price or 0.0),
        quantity=float(intent.quantity),
        time_in_force=intent.time_in_force,
        feature_snapshot_id=intent.source_features_reference,
        risk_metadata={"registry_id": intent.registry_id, "risk_budget_id": intent.risk_budget_id},
        reason_code=intent.reason_code,
    )


def _decision_from_safety_result(intent: TradeManagerOrderIntent, result: SafetyResult) -> TradeManagerRiskDecision:
    return TradeManagerRiskDecision(
        allowed=False,
        reason="PRODUCTION_SAFETY_REJECTED",
        action=result.action.name,
        monitor_name=result.monitor_name,
        order_intent_id=intent.order_intent_id,
        model_id=intent.model_id,
        details={"production_safety": _safety_result_payload(result)},
    )


def _reject(intent: TradeManagerOrderIntent, reason: str, details: dict[str, Any]) -> TradeManagerRiskDecision:
    return TradeManagerRiskDecision(
        allowed=False,
        reason=reason,
        action=HaltAction.REJECT_ORDER.name,
        monitor_name="TradeManagerRiskLayer",
        order_intent_id=intent.order_intent_id,
        model_id=intent.model_id,
        details=details,
    )


def _safety_result_payload(result: SafetyResult) -> dict[str, Any]:
    return {
        "allowed": result.allowed,
        "halt_reason": result.halt_reason,
        "action": result.action.name,
        "monitor_name": result.monitor_name,
        "details": dict(result.details),
    }
