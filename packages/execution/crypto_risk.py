"""C7 wiring per CRYPTO_LIVE.md §4 — risk decision enforced at the single crypto submission gate; kill switch fired → all orders blocked + cancel-all ≤ 1 s."""
from __future__ import annotations

import dataclasses
import os
import time
import types
from typing import Any, Callable

from execution.interfaces import OrderIntent as ExecutionOrderIntent
from trade_manager.order_intent import TradeManagerOrderIntent
from trade_manager.risk_layer import (
    TradeManagerRiskConfig,
    TradeManagerRiskContext,
    TradeManagerRiskLayer,
)


class CryptoKillSwitch:
    """Process-local kill switch keyed to env LIVE_KILL_SWITCH.

    Absent env = "fired" (fail-closed).
    "armed"/"1"/"true" (case-insensitive) = trading allowed.

    State is PROCESS-LOCAL: os.environ writes do not cross process boundaries.
    Out-of-process watchdogs need their own signal channel (file or socket);
    that channel is planned with the C8 session runner.
    """

    def status(self) -> str:
        val = os.environ.get("LIVE_KILL_SWITCH", "")
        return "armed" if val.lower() in ("armed", "1", "true") else "fired"

    def is_armed(self) -> bool:
        return self.status() == "armed"

    def fire(self) -> None:
        """Set process-local halt signal. Operator/external automation can flip upstream env."""
        os.environ["LIVE_KILL_SWITCH"] = "fired"


def crypto_risk_config(**overrides: Any) -> TradeManagerRiskConfig:
    """Build TradeManagerRiskConfig with crypto-appropriate defaults.

    kill_switch_status is NOT baked here — refreshed live per call in build_crypto_risk_check.
    """
    defaults: dict[str, Any] = {
        "symbol_eligibility": ("BTCUSDT", "tBTCUSD", "BTC_USD"),
        "instrument_eligibility": ("BTCUSDT", "tBTCUSD", "BTC_USD"),
    }
    raw_max_order = os.environ.get("LIVE_MAX_ORDER_SIZE")
    if raw_max_order:
        defaults["max_order_size"] = float(raw_max_order)
    raw_daily_loss = os.environ.get("LIVE_DAILY_LOSS_LIMIT")
    if raw_daily_loss:
        defaults["max_daily_loss"] = float(raw_daily_loss)
    defaults.update(overrides)
    return TradeManagerRiskConfig(**defaults)


def build_crypto_risk_check(
    adapter: Any,
    *,
    config: TradeManagerRiskConfig | None = None,
    kill_switch: CryptoKillSwitch | None = None,
    model_id: str = "CRYPTO",
    strategy_id: str = "crypto_lane",
    execution_mode: str | None = None,
) -> Callable[[ExecutionOrderIntent], bool]:
    """Return a closure that evaluates TradeManagerRiskLayer for each execution OrderIntent.

    Kill-switch gate is checked first (gate-level halt before risk layer).
    Risk layer's own KILL_SWITCH_NOT_ARMED check also fires (defense in depth).

    LIVE_MAX_ORDER_SIZE and LIVE_DAILY_LOSS_LIMIT are read once at build time via
    crypto_risk_config(), not per call.

    Monitors wired but vacuous until C8 feeds live book/position state:
      - spread check (bid == ask == intent.price, distance 0)
      - price-band check (reference == intent.price, distance 0)
      - position/exposure check (context defaults 0)

    Substantively enforced pre-C8: kill-switch, symbol/instrument eligibility,
    max_order_size, stale-signal guard, and timestamp guard.
    """
    if kill_switch is None:
        kill_switch = CryptoKillSwitch()
    base_config = config if config is not None else crypto_risk_config()

    # Minimal active_model stand-in: evaluate only uses allowed_instruments + run_id.
    # allowed_instruments=() means no instrument restriction from the model side.
    _active_model = types.SimpleNamespace(
        run_id="crypto_lane",
        model_id=model_id,
        allowed_instruments=(),
    )

    _mode = execution_mode  # captured at build time; None → read from safety per call
    _layer_cache: list[tuple[str, TradeManagerRiskLayer]] = []  # [(status, layer)]

    _TICK_MAP: dict[str, float] = {
        "tBTCUSD": 1.0,
        "BTCUSDT": 1.0,
        "BTC_USD": 1.0,
    }

    def _get_layer(ks_status: str) -> TradeManagerRiskLayer:
        if _layer_cache and _layer_cache[0][0] == ks_status:
            return _layer_cache[0][1]
        cfg = dataclasses.replace(base_config, kill_switch_status=ks_status)
        layer = TradeManagerRiskLayer(cfg)
        _layer_cache.clear()
        _layer_cache.append((ks_status, layer))
        return layer

    def check(intent: ExecutionOrderIntent) -> bool:
        # Gate-level halt — fired switch blocks everything before risk layer.
        if not kill_switch.is_armed():
            return False

        # Fail-closed on unstamped intents: the risk layer skips its stale-signal
        # check when timestamp is falsy (risk_layer.py "if ... and intent.timestamp"),
        # so a zero timestamp would silently bypass signal-freshness enforcement.
        if int(getattr(intent, "timestamp_ns", 0) or 0) <= 0:
            return False

        ks_status = kill_switch.status()

        tm_intent = TradeManagerOrderIntent(
            order_intent_id=intent.intent_id,
            registry_id=getattr(intent, "feature_snapshot_id", "") or "",
            model_id=intent.model_id or model_id,
            strategy_id=intent.strategy_id or strategy_id,
            signal_id=intent.intent_id,
            timestamp=intent.timestamp_ns,
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(intent.quantity),
            order_type=intent.order_type,
            limit_price=float(intent.price) if intent.price else None,
            time_in_force=getattr(intent, "time_in_force", "GTC") or "GTC",
            expected_edge=0.0,
            risk_budget_id=(intent.risk_metadata or {}).get("risk_budget_id", "default"),
            reason_code=getattr(intent, "reason_code", "") or "",
            execution_profile={"type": "CRYPTO"},
            latency_profile={"budget_ms": getattr(intent, "latency_budget_ms", 1.0)},
            source_features_reference=getattr(intent, "feature_snapshot_id", "") or "",
        )

        now_ns = time.time_ns()
        mode: str
        if _mode is not None:
            mode = _mode
        else:
            from execution import safety as _safety
            mode = _safety.execution_mode()

        # intent arrival treated as signal freshness until C8 wires real signal timestamps from the live feature feed.
        last_signal_ns = intent.timestamp_ns
        context = TradeManagerRiskContext(
            adapter=adapter,
            execution_mode=mode,
            system_clock_ns=now_ns,
            exchange_clock_ns=now_ns,
            # last_market_data_ns: workstation wiring; real feeds populate at C8.
            last_market_data_ns=now_ns,
            last_signal_ns=last_signal_ns,
            tick_size=_TICK_MAP.get(intent.symbol, 0.01),
            # reference/bid/ask from intent.price: honest approximation until C8 wires live book.
            reference_price=float(intent.price) if intent.price else None,
            bid_price=float(intent.price) if intent.price else None,
            ask_price=float(intent.price) if intent.price else None,
            has_liquidity=True,
        )

        layer = _get_layer(ks_status)
        decision = layer.evaluate(_active_model, tm_intent, context)
        return decision.allowed

    return check


def execute_kill_sequence(
    adapter: Any,
    kill_switch: CryptoKillSwitch,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    """Fire kill switch, best-effort cancel-all, return timing dict.

    Budget: 1 s per CRYPTO_LIVE.md §4.3.
    A swallowed cancel failure must never read as a passed kill drill
    (CRYPTO_LIVE.md §4.3 fail-closed).
    """
    t0 = clock()
    kill_switch.fire()
    if hasattr(adapter, "cancel_all_open"):
        cancel_ok = adapter.cancel_all_open()
    else:
        adapter.close()  # cannot verify; close is best-effort only
        cancel_ok = False
    elapsed = clock() - t0
    return {
        "fired": True,
        "cancel_ok": cancel_ok,
        "cancel_all_elapsed_s": elapsed,
        "within_budget": elapsed <= 1.0,
        "pass": bool(cancel_ok and elapsed <= 1.0),
    }
