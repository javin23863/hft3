from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml


def _now_ns() -> int:
    return time.perf_counter_ns()


def _wall_ns() -> int:
    return time.time_ns()

from ._rithmic_api_bridge import (
    MarketDataEvent,
    ConnectionConfig,
    OrderEvent,
    RithmicApiBridge,
    RithmicApiError,
    RithmicApiLibraryNotFoundError,
)
from .base import ConnectorInterface


__all__ = [
    "RithmicApiConnector",
    "RithmicApiBridge",
    "RithmicApiError",
    "RithmicApiLibraryNotFoundError",
    "ConnectionConfig",
    "OrderEvent",
]


_SIDE_MAP = {"BUY": "B", "B": "B", "SELL": "A", "A": "A"}

_BRIDGE_TO_DAEMON_EVENT = {
    "A": "order_ack",
    "F": "fill",
    "C": "cancel",
    "M": "order_replace",
    "R": "reject",
    "X": "order_failure",
}


class RithmicApiConnector(ConnectorInterface):
    """Direct R|API Plus connector for the Rithmic test environment.

    Loads config from ``rithmic_api_test.yaml``, resolves SSL cert paths, and
    delegates the engine calls to ``librithmic_gateway_shared.so`` via ctypes.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).resolve().parents[2] / "config" / "rithmic_api_test.yaml"
        self._config_path = Path(config_path)
        self._cfg: dict[str, Any] = {}
        self._ssl_cert_path: Path | None = None
        self._bridge: RithmicApiBridge | None = None
        self._connected = False
        self._submit_seq = 0
        self._pending_submits: list[dict[str, Any]] = []
        self._inflight_submits: list[dict[str, Any]] = []
        self._last_symbol = ""
        self._last_exchange = ""
        self._load_config()

    def _load_config(self) -> None:
        with open(self._config_path, encoding="utf-8") as fh:
            self._cfg = yaml.safe_load(fh)
        self._ssl_cert_path = self._resolve_ssl_cert_path()

    def _resolve_ssl_cert_path(self) -> Path:
        repo_root = self._find_repo_root()
        api_version = self._cfg.get("api_version", "13.7.0.0")
        cert_rel = self._cfg.get("ssl", {}).get(
            "cert_file", "rithmic_ssl_cert_auth_params"
        )
        return repo_root / "rithmic_gateway" / "RApiPlus" / api_version / "etc" / cert_rel

    def _find_repo_root(self) -> Path:
        current = self._config_path.resolve().parent
        for _ in range(20):
            if (current / ".git").exists() or (current / "pyproject.toml").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return self._config_path.resolve().parent.parent.parent.parent

    def _build_connection_config(self) -> ConnectionConfig:
        env_block = self._cfg.get("environment", {})
        env_vars: list[str] = [f"{k}={v}" for k, v in env_block.items()]
        if self._ssl_cert_path is not None:
            env_vars.append(f"MML_SSL_CLNT_AUTH_FILE={self._ssl_cert_path}")
        username = os.environ.get("RITHMIC_USERNAME", "")
        if username:
            env_vars.append(f"USER={username}")

        engine = self._cfg.get("engine_params", {}) or {}
        login = self._cfg.get("login_params", {}) or {}
        repository_login = self._cfg.get("repository_login", {}) or {}

        md = login.get("sMdCnnctPt") or ""
        ts = login.get("sTsCnnctPt") or ""
        pnl = login.get("sPnlCnnctPt") or ""
        ih = login.get("sIhCnnctPt") or ""
        rep = repository_login.get("sCnnctPt") or ""

        env_name = (
            self._cfg.get("system")
            or self._cfg.get("environment_name")
            or "Rithmic Test"
        )
        log_file_path = (
            Path(self._find_repo_root())
            / "runtime"
            / "rithmic_trial"
            / "rithmic_api.log"
        )
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = str(log_file_path)

        return ConnectionConfig(
            environment=str(env_name),
            username=os.environ.get("RITHMIC_USERNAME", "") or "",
            password=os.environ.get("RITHMIC_PASSWORD", "") or "",
            app_name=str(engine.get("app_name", "HFT3")),
            app_version=str(engine.get("app_version", "1.0")),
            ssl_cert_path=str(self._ssl_cert_path) if self._ssl_cert_path else "",
            log_file_path=log_file,
            md_connect_point=str(md),
            ts_connect_point=str(ts),
            rep_connect_point=str(rep),
            pnl_connect_point=str(pnl),
            ih_connect_point=str(ih),
            env_vars=env_vars,
        )

    def connect(self) -> None:
        if self._connected:
            return
        username = os.environ.get("RITHMIC_USERNAME")
        password = os.environ.get("RITHMIC_PASSWORD")
        if not username or not password:
            raise EnvironmentError(
                "RITHMIC_USERNAME and RITHMIC_PASSWORD must be set in the environment"
            )
        if self._ssl_cert_path and not self._ssl_cert_path.exists():
            raise FileNotFoundError(
                f"SSL cert file not found: {self._ssl_cert_path}. "
                "Download the RApiPlus SDK and place it under rithmic_gateway/."
            )
        bridge = RithmicApiBridge.load()
        cfg = self._build_connection_config()
        bridge.create(cfg).initialize().connect()
        self._bridge = bridge
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            try:
                bridge.disconnect()
            finally:
                bridge.destroy()

    def subscribe_mbo(self, symbol: str, exchange: str) -> None:
        if not self._connected or self._bridge is None:
            raise RuntimeError("Not connected — call connect() first")
        self._bridge.subscribe_mbo(symbol, exchange)
        self._last_symbol = symbol
        self._last_exchange = exchange

    def send_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        if not self._connected or self._bridge is None:
            raise RuntimeError("Not connected — call connect() first")
        key = side.upper() if isinstance(side, str) else side
        if key not in _SIDE_MAP:
            raise ValueError(f"unknown side: {side!r}; expected one of {sorted(_SIDE_MAP)}")
        side_char = _SIDE_MAP[key]
        self._submit_seq += 1
        client_order_id = f"hft3-{self._submit_seq}-{_now_ns()}"
        emit_mono_ns = _now_ns()
        emit_wall_ns = _wall_ns()
        self._bridge.send_order(symbol, side_char, qty, price, user_msg=client_order_id)
        submit = {
            "client_order_id": client_order_id,
            "order_id": client_order_id,
            "symbol": symbol,
            "exchange": self._last_exchange or "CME",
            "side": key,
            "qty": int(qty),
            "size": int(qty),
            "price": float(price),
            "ts_emit_ns": emit_mono_ns,
            "local_monotonic_receive_ns": emit_mono_ns,
            "local_receive_timestamp_ns": emit_wall_ns,
        }
        self._pending_submits.append(submit)
        self._inflight_submits.append(submit)
        self._last_symbol = symbol
        return client_order_id

    def cancel_order(self, order_id: str) -> None:
        if not self._connected or self._bridge is None:
            raise RuntimeError("Not connected — call connect() first")
        self._bridge.cancel_order(order_id)

    def poll_events(self) -> list[dict[str, Any]]:
        if self._bridge is None:
            return []
        out: list[dict[str, Any]] = []
        for _ in range(1000):
            ev = self._bridge.try_pop_event()
            if ev is None:
                break
            out.append(self._adapt_market_event(ev))
        if len(out) < 1000:
            out.extend(self.poll_order_events(limit=1000 - len(out)))
        return out

    def poll_order_events(self, limit: int = 1000) -> list[dict[str, Any]]:
        if self._bridge is None:
            return []
        out: list[dict[str, Any]] = []

        while self._pending_submits and len(out) < limit:
            submit = self._pending_submits[0]
            submit_record = {
                "event_type": "order_submit",
                "order_id": submit["order_id"],
                "client_order_id": submit["client_order_id"],
                "symbol": submit["symbol"],
                "exchange": submit["exchange"],
                "side": submit["side"],
                "qty": submit["qty"],
                "size": submit["size"],
                "price": submit["price"],
                "ts_emit_ns": submit["ts_emit_ns"],
                "local_monotonic_receive_ns": submit["local_monotonic_receive_ns"],
                "local_receive_timestamp_ns": submit["local_receive_timestamp_ns"],
            }
            out.append(submit_record)
            self._pending_submits.pop(0)

        for _ in range(limit - len(out)):
            ev = self._bridge.try_pop_order_event()
            if ev is None:
                break
            out.append(self._adapt_order_event(ev))
        return out

    def _adapt_market_event(self, ev: MarketDataEvent) -> dict[str, Any]:
        recv_mono_ns = _now_ns()
        recv_wall_ns = _wall_ns()
        if ev.action == "T":
            event_type = "trade"
        elif ev.action == "M":
            event_type = "quote"
        else:
            event_type = "depth"
        out: dict[str, Any] = {
            "event_type": event_type,
            "bridge_event_type": ev.action,
            "symbol": self._last_symbol,
            "exchange": self._last_exchange,
            "exchange_timestamp_ns": ev.timestamp_ns or None,
            "timestamp_ns": ev.timestamp_ns,
            "order_id": ev.order_id,
            "side": ev.side,
            "price": ev.price,
            "size": ev.size,
            "local_monotonic_receive_ns": recv_mono_ns,
            "local_receive_timestamp_ns": recv_wall_ns,
        }
        if event_type == "quote" and ev.side == "B":
            out["bid_price"] = ev.price
            out["bid_size"] = ev.size
        elif event_type == "quote" and ev.side == "A":
            out["ask_price"] = ev.price
            out["ask_size"] = ev.size
        return out

    def _adapt_order_event(self, ev: OrderEvent) -> dict[str, Any]:
        recv_mono_ns = int(ev.callback_monotonic_ns or _now_ns())
        recv_wall_ns = int(ev.callback_wall_ns or _wall_ns())
        bridge_evt = ev.event_type
        daemon_evt = _BRIDGE_TO_DAEMON_EVENT.get(bridge_evt, "order_status")
        broker_order_id = str(ev.order_id) if ev.order_id != 0 else ""
        client_order_id = ev.user_msg or ev.tag or ""
        inflight = self._find_inflight(client_order_id)
        if not client_order_id and inflight is not None:
            client_order_id = str(inflight["client_order_id"])
        order_id = client_order_id or broker_order_id or f"unknown-{ev.timestamp_ns}"
        if daemon_evt in {"order_ack", "reject", "order_failure"} and inflight is not None:
            self._mark_inflight_observed(inflight["client_order_id"])
        return {
            "event_type": daemon_evt,
            "bridge_event_type": bridge_evt,
            "order_id": str(order_id),
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "symbol": inflight.get("symbol", self._last_symbol) if inflight else self._last_symbol,
            "exchange": inflight.get("exchange", self._last_exchange) if inflight else self._last_exchange,
            "side": ev.side,
            "order_type": ev.order_type,
            "price": ev.price,
            "size": ev.size,
            "filled_size": ev.filled_size,
            "total_filled": ev.total_filled,
            "total_unfilled": ev.total_unfilled,
            "user_msg": ev.user_msg,
            "tag": ev.tag,
            "exchange_timestamp_ns": ev.timestamp_ns or None,
            "timestamp_ns": ev.timestamp_ns,
            "local_monotonic_receive_ns": recv_mono_ns,
            "local_receive_timestamp_ns": recv_wall_ns,
        }

    def _find_inflight(self, client_order_id: str) -> dict[str, Any] | None:
        if client_order_id:
            for submit in self._inflight_submits:
                if submit["client_order_id"] == client_order_id:
                    return submit
        return self._inflight_submits[0] if self._inflight_submits else None

    def _mark_inflight_observed(self, client_order_id: str) -> None:
        self._inflight_submits = [
            submit for submit in self._inflight_submits
            if submit["client_order_id"] != client_order_id
        ]

    def detected_event_types(self) -> set[str]:
        return {"order_submit", "order_ack", "fill", "cancel",
                "order_replace", "reject", "order_failure"}

    def limitations(self) -> dict[str, Any]:
        return {
            "connector": "rithmic_api",
            "status": "ctypes_bridge" if self._connected else "skeleton",
            "note": "Bridges to librithmic_gateway_shared.so via ctypes; "
                    "event-type detection left to downstream normalization",
        }

    def close(self) -> None:
        self.disconnect()
