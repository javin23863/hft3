from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ._rithmic_api_bridge import (
    ConnectionConfig,
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
]


_SIDE_MAP = {"BUY": "B", "B": "B", "SELL": "A", "A": "A"}


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
        connect_points = self._cfg.get("connect_points", {}) or {}

        md = (
            connect_points.get("md")
            or login.get("sMdCnnctPt")
            or ""
        )
        ts = (
            connect_points.get("ts")
            or login.get("sTsCnnctPt")
            or ""
        )
        rep = (
            connect_points.get("rep")
            or connect_points.get("ih")
            or login.get("sPnlCnnctPt")
            or login.get("sIhCnnctPt")
            or ""
        )

        env_name = (
            self._cfg.get("system")
            or self._cfg.get("environment_name")
            or "Rithmic Test"
        )
        log_file = str(
            Path(self._find_repo_root())
            / "runtime"
            / "rithmic_trial"
            / "rithmic_api.log"
        )

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

    def send_order(self, symbol: str, side: str, qty: int, price: float) -> None:
        if not self._connected or self._bridge is None:
            raise RuntimeError("Not connected — call connect() first")
        key = side.upper() if isinstance(side, str) else side
        if key not in _SIDE_MAP:
            raise ValueError(f"unknown side: {side!r}; expected one of {sorted(_SIDE_MAP)}")
        side_char = _SIDE_MAP[key]
        self._bridge.send_order(symbol, side_char, qty, price)

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
            out.append(ev.to_dict())
        return out

    def detected_event_types(self) -> set[str]:
        return set()

    def limitations(self) -> dict[str, Any]:
        return {
            "connector": "rithmic_api",
            "status": "ctypes_bridge" if self._connected else "skeleton",
            "note": "Bridges to librithmic_gateway_shared.so via ctypes; "
                    "event-type detection left to downstream normalization",
        }

    def close(self) -> None:
        self.disconnect()
