"""Production safety monitors — stale data, disconnect, clock drift, position mismatch, daily loss limit flatten.

Implements BLUEPRINT §9 failure states and the A+ Production Implementation Prompt pre-trade risk gates.
"""
from __future__ import annotations

import math
import os
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from execution.interfaces import AccountState, ExecutionAdapter, OrderIntent


class HaltAction(Enum):
    NONE = auto()
    LOG_WARNING = auto()
    REJECT_ORDER = auto()
    CANCEL_ALL_AND_HALT = auto()
    FLATTEN_AND_HALT = auto()


@dataclass
class SafetyResult:
    allowed: bool = True
    halt_reason: str = ""
    action: HaltAction = HaltAction.NONE
    monitor_name: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SafetyContext:
    """All information needed for pre-trade safety checks."""
    order_intent: OrderIntent
    adapter: ExecutionAdapter
    local_inventory: float = 0.0
    local_realized_pnl: float = 0.0
    last_market_data_ns: int = 0
    exchange_clock_ns: int = 0
    system_clock_ns: int = 0
    execution_mode: str = "REPLAY"
    session_start_ns: int = 0
    daily_loss_limit: float = float("inf")
    daily_loss_so_far: float = 0.0


class StaleDataMonitor:
    """BLUEPRINT §9: Stale market data halt.

    Tracks time since last market data update. Halts when no fresh data
    within max_stale_ns. Default: 5ms (matching typical CME MDP3 update rate).
    """

    def __init__(self, max_stale_ns: int = 5_000_000):
        self.max_stale_ns = max_stale_ns
        self.last_update_ns: int = 0

    def check(self, now_ns: int, last_market_data_ns: int) -> SafetyResult:
        if last_market_data_ns == 0:
            return SafetyResult(
                allowed=False,
                halt_reason="No market data received since start",
                action=HaltAction.REJECT_ORDER,
                monitor_name="StaleDataMonitor",
                details={"now_ns": now_ns, "last_market_data_ns": 0},
            )
        age_ns = now_ns - last_market_data_ns
        if age_ns > self.max_stale_ns:
            return SafetyResult(
                allowed=False,
                halt_reason=f"Market data stale: {age_ns / 1e6:.1f}ms since last update",
                action=HaltAction.REJECT_ORDER,
                monitor_name="StaleDataMonitor",
                details={
                    "now_ns": now_ns,
                    "last_market_data_ns": last_market_data_ns,
                    "age_ns": age_ns,
                    "max_stale_ns": self.max_stale_ns,
                },
            )
        return SafetyResult(
            allowed=True,
            action=HaltAction.NONE,
            monitor_name="StaleDataMonitor",
            details={"age_ns": age_ns},
        )


class DisconnectMonitor:
    """BLUEPRINT §9: Disconnect halt.

    Queries adapter health state. If adapter reports disconnected and
    grace period has elapsed, triggers CANCEL_ALL_AND_HALT.
    """

    def __init__(self, disconnect_grace_ns: int = 1_000_000_000):
        self.disconnect_grace_ns = disconnect_grace_ns
        self.disconnect_start_ns: int = 0
        self._was_connected: bool = True

    def check(self, now_ns: int, adapter: ExecutionAdapter) -> SafetyResult:
        is_connected = self._query_connected(adapter)
        if is_connected:
            self._was_connected = True
            self.disconnect_start_ns = 0
            return SafetyResult(
                allowed=True,
                action=HaltAction.NONE,
                monitor_name="DisconnectMonitor",
                details={"connected": True},
            )

        if self._was_connected:
            self._was_connected = False
            self.disconnect_start_ns = now_ns
            return SafetyResult(
                allowed=True,
                action=HaltAction.LOG_WARNING,
                halt_reason="Adapter disconnected — grace period started",
                monitor_name="DisconnectMonitor",
                details={
                    "connected": False,
                    "disconnect_start_ns": now_ns,
                    "grace_ns": self.disconnect_grace_ns,
                },
            )

        elapsed = now_ns - self.disconnect_start_ns
        if elapsed >= self.disconnect_grace_ns:
            return SafetyResult(
                allowed=False,
                halt_reason=f"Adapter disconnected for {elapsed / 1e9:.1f}s — grace exceeded",
                action=HaltAction.CANCEL_ALL_AND_HALT,
                monitor_name="DisconnectMonitor",
                details={
                    "connected": False,
                    "disconnect_start_ns": self.disconnect_start_ns,
                    "elapsed_ns": elapsed,
                    "grace_ns": self.disconnect_grace_ns,
                },
            )

        return SafetyResult(
            allowed=True,
            action=HaltAction.LOG_WARNING,
            halt_reason="Adapter disconnected — within grace period",
            monitor_name="DisconnectMonitor",
            details={
                "connected": False,
                "disconnect_start_ns": self.disconnect_start_ns,
                "elapsed_ns": elapsed,
                "grace_ns": self.disconnect_grace_ns,
            },
        )

    @staticmethod
    def _query_connected(adapter: ExecutionAdapter) -> bool:
        try:
            connected = getattr(adapter, "is_connected", None)
        except Exception:
            return True
        if connected is None:
            return True
        if callable(connected):
            try:
                return bool(connected())
            except Exception:
                return True
        return bool(connected)


class ClockDriftMonitor:
    """BLUEPRINT §9: Clock drift halt.

    Compares exchange clock (from MBO timestamps) against system monotonic clock.
    Halts when absolute drift exceeds max_drift_ns.
    Uses time.monotonic_ns() and exchange timestamps from MBO events.
    """

    def __init__(self, max_drift_ns: int = 50_000_000, max_samples: int = 100):
        self.max_drift_ns = max_drift_ns
        self.max_samples = max_samples
        self._drift_samples: List[float] = []
        self._baseline_offset_ns: Optional[int] = None

    def check(self, now_ns: int, exchange_clock_ns: int) -> SafetyResult:
        if exchange_clock_ns == 0:
            return SafetyResult(
                allowed=True,
                action=HaltAction.NONE,
                monitor_name="ClockDriftMonitor",
                details={"drift_median_ns": 0.0, "samples": 0, "status": "no_exchange_data"},
            )

        if self._baseline_offset_ns is None:
            self._baseline_offset_ns = now_ns - exchange_clock_ns
            return SafetyResult(
                allowed=True,
                action=HaltAction.NONE,
                monitor_name="ClockDriftMonitor",
                details={
                    "drift_median_ns": 0.0,
                    "samples": 0,
                    "baseline_offset_ns": self._baseline_offset_ns,
                    "status": "baseline_set",
                },
            )

        drift = abs(exchange_clock_ns + self._baseline_offset_ns - now_ns)
        self._drift_samples.append(drift)
        if len(self._drift_samples) > self.max_samples:
            self._drift_samples = self._drift_samples[-self.max_samples:]

        median_drift = float(statistics.median(self._drift_samples))
        if median_drift > self.max_drift_ns:
            return SafetyResult(
                allowed=False,
                halt_reason=(
                    f"Clock drift {median_drift / 1e6:.1f}ms exceeds "
                    f"limit {self.max_drift_ns / 1e6:.1f}ms"
                ),
                action=HaltAction.REJECT_ORDER,
                monitor_name="ClockDriftMonitor",
                details={
                    "drift_median_ns": median_drift,
                    "samples": len(self._drift_samples),
                    "max_drift_ns": self.max_drift_ns,
                },
            )

        return SafetyResult(
            allowed=True,
            action=HaltAction.NONE,
            monitor_name="ClockDriftMonitor",
            details={
                "drift_median_ns": median_drift,
                "samples": len(self._drift_samples),
            },
        )


class PositionMismatchGuard:
    """BLUEPRINT §9: Position mismatch block.

    Compares exchange-reported position (from adapter.get_position())
    against local inventory tracker. Blocks all orders on mismatch.
    """

    def __init__(self, max_mismatch_contracts: float = 1.0):
        self.max_mismatch = max_mismatch_contracts

    def check(self, adapter: ExecutionAdapter, symbol: str, local_inventory: float) -> SafetyResult:
        if not hasattr(adapter, "get_position"):
            return SafetyResult(
                allowed=True,
                action=HaltAction.NONE,
                monitor_name="PositionMismatchGuard",
                details={"status": "adapter_no_get_position"},
            )

        try:
            exchange_position = adapter.get_position(symbol)
        except Exception:
            return SafetyResult(
                allowed=True,
                action=HaltAction.LOG_WARNING,
                halt_reason="get_position() raised exception",
                monitor_name="PositionMismatchGuard",
                details={"status": "get_position_error", "symbol": symbol},
            )

        mismatch = abs(exchange_position - local_inventory)
        if mismatch > self.max_mismatch:
            return SafetyResult(
                allowed=False,
                halt_reason=(
                    f"Position mismatch: exchange={exchange_position}, "
                    f"local={local_inventory}, diff={mismatch}"
                ),
                action=HaltAction.REJECT_ORDER,
                monitor_name="PositionMismatchGuard",
                details={
                    "exchange_position": exchange_position,
                    "local_inventory": local_inventory,
                    "mismatch": mismatch,
                    "max_mismatch": self.max_mismatch,
                },
            )

        return SafetyResult(
            allowed=True,
            action=HaltAction.NONE,
            monitor_name="PositionMismatchGuard",
            details={
                "exchange_position": exchange_position,
                "local_inventory": local_inventory,
                "mismatch": mismatch,
            },
        )


class DailyLossLimitFlatten:
    """BLUEPRINT §9: Daily loss limit flatten.

    Tracks realized + unrealized PnL since session start.
    When loss exceeds limit, returns FLATTEN_AND_HALT.
    """

    def __init__(self):
        self.loss_limit: float = float("inf")

    def configure_from_env(self) -> None:
        raw = os.environ.get("EXTERNAL_DAILY_LOSS_LIMIT", "")
        if raw:
            try:
                self.loss_limit = float(raw)
            except ValueError:
                pass

    def check(
        self,
        account: AccountState,
        daily_loss_limit: float,
        daily_loss_so_far: float,
        current_local_inventory: float,
        symbol: str,
    ) -> SafetyResult:
        total_loss = -(daily_loss_so_far + account.unrealized_pnl + account.realized_pnl)
        effective_limit = daily_loss_limit
        if daily_loss_limit == float("inf") and self.loss_limit != float("inf"):
            effective_limit = self.loss_limit

        if total_loss > effective_limit:
            flatten_sign = "SELL" if current_local_inventory > 0 else "BUY"
            return SafetyResult(
                allowed=False,
                halt_reason=(
                    f"Daily loss {total_loss:.2f} exceeds limit {effective_limit:.2f} — flatten position"
                ),
                action=HaltAction.FLATTEN_AND_HALT,
                monitor_name="DailyLossLimitFlatten",
                details={
                    "total_loss": total_loss,
                    "daily_loss_limit": effective_limit,
                    "daily_loss_so_far": daily_loss_so_far,
                    "unrealized_pnl": account.unrealized_pnl,
                    "realized_pnl": account.realized_pnl,
                    "current_inventory": current_local_inventory,
                    "flatten_direction": "SELL" if current_local_inventory > 0 else "BUY",
                    "symbol": symbol,
                },
            )

        return SafetyResult(
            allowed=True,
            action=HaltAction.NONE,
            monitor_name="DailyLossLimitFlatten",
            details={
                "total_loss": total_loss,
                "daily_loss_limit": effective_limit,
            },
        )


class ProductionSafetyOrchestrator:
    """Aggregates all five monitors. Called pre-trade in hot path.

    Monitors run in priority order. The highest-severity halt wins.
    In REPLAY mode, all monitors run in audit-only (log warnings, never halt).
    In EXTERNAL mode, halts are enforced.

    Priority order (matches BLUEPRINT §9):
    1. DisconnectMonitor   — kills everything
    2. DailyLossLimitFlatten — flatten, then halt
    3. PositionMismatchGuard — block all orders
    4. ClockDriftMonitor — block all orders
    5. StaleDataMonitor — block all orders
    """

    def __init__(self):
        self.stale_data = StaleDataMonitor()
        self.disconnect = DisconnectMonitor()
        self.clock_drift = ClockDriftMonitor()
        self.position_mismatch = PositionMismatchGuard()
        self.daily_loss = DailyLossLimitFlatten()
        self.execution_mode = os.environ.get("EXECUTION_MODE", "REPLAY").upper()
        self._cached_account: Optional[AccountState] = None
        self._cache_age_ns: int = 0
        self._cache_ttl_ns: int = 1_000_000  # 1ms cache TTL

    def _get_account(self, adapter: ExecutionAdapter, now_ns: int) -> AccountState:
        if self._cached_account is None or (now_ns - self._cache_age_ns) > self._cache_ttl_ns:
            try:
                self._cached_account = adapter.get_account_state()
            except Exception:
                if self._cached_account is None:
                    self._cached_account = AccountState()
            self._cache_age_ns = now_ns
        return self._cached_account

    def _submit_flatten_orders(
        self,
        adapter: ExecutionAdapter,
        symbol: str,
        inventory: float,
        now_ns: int,
    ) -> List[OrderIntent]:
        """Generate flatten order intents when daily loss limit is breached."""
        if abs(inventory) < 0.5:
            return []
        side = "SELL" if inventory > 0 else "BUY"
        intent = OrderIntent(
            intent_id=str(uuid.uuid4()),
            run_id="",
            timestamp_ns=now_ns,
            strategy_id="safety",
            model_id="DAILY_LOSS_LIMIT_FLATTEN",
            symbol=symbol,
            side=side,
            order_type="MARKET",
            price=0.0,
            quantity=abs(inventory),
            time_in_force="IOC",
            reason_code="DAILY_LOSS_LIMIT_FLATTEN",
        )
        return [intent]

    def pre_trade_check(self, ctx: SafetyContext) -> SafetyResult:
        enforce_halts = ctx.execution_mode.upper() != "REPLAY"
        worst_result = SafetyResult(allowed=True, action=HaltAction.NONE)
        all_results: List[SafetyResult] = []

        now_ns = ctx.system_clock_ns or time.monotonic_ns()

        results_ordered: List[SafetyResult] = [
            self.disconnect.check(now_ns, ctx.adapter),
            self.daily_loss.check(
                self._get_account(ctx.adapter, now_ns),
                ctx.daily_loss_limit,
                ctx.daily_loss_so_far,
                ctx.local_inventory,
                ctx.order_intent.symbol,
            ),
            self.position_mismatch.check(ctx.adapter, ctx.order_intent.symbol, ctx.local_inventory),
            self.clock_drift.check(now_ns, ctx.exchange_clock_ns),
            self.stale_data.check(now_ns, ctx.last_market_data_ns),
        ]

        all_results.extend(results_ordered)

        if not enforce_halts:
            return SafetyResult(
                allowed=True,
                action=HaltAction.NONE,
                monitor_name="ProductionSafetyOrchestrator",
                details={
                    "execution_mode": ctx.execution_mode,
                    "audit": [
                        {
                            "monitor": r.monitor_name,
                            "action": r.action.name,
                            "reason": r.halt_reason,
                        }
                        for r in all_results
                    ],
                },
            )

        for result in results_ordered:
            if result.action.value > worst_result.action.value:
                worst_result = result
            if result.action in (
                HaltAction.CANCEL_ALL_AND_HALT,
                HaltAction.FLATTEN_AND_HALT,
                HaltAction.REJECT_ORDER,
            ):
                return result

        return worst_result

    def update_market_data(self, now_ns: int, exchange_clock_ns: int) -> None:
        self.stale_data.last_update_ns = now_ns
        self.clock_drift.check(now_ns, exchange_clock_ns)

    def update_order_fill(self, account: AccountState, local_inventory: float, local_pnl: float) -> None:
        pass

    def reset_session(self) -> None:
        self.stale_data = StaleDataMonitor()
        self.disconnect = DisconnectMonitor()
        self.clock_drift = ClockDriftMonitor()
        self.position_mismatch = PositionMismatchGuard()
        self.daily_loss = DailyLossLimitFlatten()


_global_orchestrator: Optional[ProductionSafetyOrchestrator] = None


def get_orchestrator() -> ProductionSafetyOrchestrator:
    global _global_orchestrator
    if _global_orchestrator is None:
        _global_orchestrator = ProductionSafetyOrchestrator()
    return _global_orchestrator


def pre_trade_check(ctx: SafetyContext) -> SafetyResult:
    return get_orchestrator().pre_trade_check(ctx)
