from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .base import ConnectorInterface


class RithmicApiConnector(ConnectorInterface):
    """Direct R|API Plus connector skeleton for the Rithmic test environment.

    Loads config from ``rithmic_api_test.yaml``, resolves SSL cert paths, and
    builds the MML_* environment variable array required by the RApiPlus engine.

    ctypes/cffi integration with the shared library is a separate step — these
    methods currently raise ``NotImplementedError`` for engine calls.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).resolve().parents[2] / "config" / "rithmic_api_test.yaml"
        self._config_path = Path(config_path)
        self._cfg: dict[str, Any] = {}
        self._envp: list[str] = []
        self._ssl_cert_path: Path | None = None
        self._connected = False
        self._load_config()

    def _load_config(self) -> None:
        with open(self._config_path, encoding="utf-8") as fh:
            self._cfg = yaml.safe_load(fh)
        self._ssl_cert_path = self._resolve_ssl_cert_path()
        self._envp = self._build_envp()

    def _resolve_ssl_cert_path(self) -> Path:
        """Walk up from config file to repo root, then resolve SDK SSL cert."""
        repo_root = self._find_repo_root()
        api_version = self._cfg.get("api_version", "13.7.0.0")
        cert_rel = self._cfg.get("ssl", {}).get(
            "cert_file", "rithmic_ssl_cert_auth_params"
        )
        return repo_root / "rithmic_gateway" / "RApiPlus" / api_version / "etc" / cert_rel

    def _find_repo_root(self) -> Path:
        """Walk up from config file until a marker file/directory is found."""
        current = self._config_path.resolve().parent
        for _ in range(20):
            if (current / ".git").exists() or (current / "pyproject.toml").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return self._config_path.resolve().parent.parent.parent.parent

    def _build_envp(self) -> list[str]:
        """Build MML_* environment variable list from config for RApiPlus engine."""
        env_block = self._cfg.get("environment", {})
        lines: list[str] = []
        for key, value in env_block.items():
            lines.append(f"{key}={value}")
        ssl_path = self._ssl_cert_path
        if ssl_path:
            lines.append(f"MML_SSL_CLNT_AUTH_FILE={ssl_path}")
        username = os.environ.get("RITHMIC_USERNAME", "")
        if username:
            lines.append(f"USER={username}")
        return lines

    def connect(self) -> None:
        """Prepare environment variables (envp array) for the RApiPlus engine.

        Does **not** call the shared library yet — ctypes/cffi integration is
        a separate step.
        """
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
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def subscribe_mbo(self, symbol: str, exchange: str) -> None:
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")
        raise NotImplementedError(
            "subscribe_mbo: ctypes/cffi integration with RApiPlus not yet implemented"
        )

    def send_order(self, symbol: str, side: str, qty: int, price: float) -> None:
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")
        raise NotImplementedError(
            "send_order: ctypes/cffi integration with RApiPlus not yet implemented"
        )

    def cancel_order(self, order_id: str) -> None:
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")
        raise NotImplementedError(
            "cancel_order: ctypes/cffi integration with RApiPlus not yet implemented"
        )

    def poll_events(self) -> list[dict[str, Any]]:
        return []

    def detected_event_types(self) -> set[str]:
        return set()

    def limitations(self) -> dict[str, Any]:
        return {
            "connector": "rithmic_api",
            "status": "skeleton",
            "note": "Config and envp ready; ctypes/cffi engine calls not yet wired",
        }

    def close(self) -> None:
        self.disconnect()