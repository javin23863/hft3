"""Interactive Brokers endpoint readiness for the equities lane.

The endpoint shape follows the QuantX socket-first setup:
TWS/IB Gateway paper on 7497, live on 7496, client IDs 17/18, and Client
Portal treated as auxiliary rather than the daily headless path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml


DEFAULT_CONFIG_REL = Path("packages/equities_lane/config/ibkr_endpoint.yaml")
DEFAULT_STATUS_REL = Path("runtime/equities_lane/ibkr_endpoint_status.json")


SocketProbe = Callable[[str, int, float], bool]
ALLOWED_ENV_PREFIXES = ("IBKR_",)
ALLOWED_ENV_KEYS = {
    "BROKER_SOCKET_ENABLED",
    "MARKET_DATA_PROVIDER",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_config_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / DEFAULT_CONFIG_REL


def default_status_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / DEFAULT_STATUS_REL


def load_endpoint_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"IBKR endpoint config not found: {cfg_path}")
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"IBKR endpoint config must be a mapping: {cfg_path}")
    return payload


def _resolve_config_path(repo_root: str | Path, config_path: str | Path | None = None) -> Path:
    path = Path(config_path) if config_path else default_config_path(repo_root)
    if not path.is_absolute():
        path = Path(repo_root) / path
    return path


def _expand_local_path(raw: str) -> Path:
    text = os.path.expandvars(str(raw))
    text = text.replace("%USERPROFILE%", str(Path.home()))
    text = text.replace("%HOME%", str(Path.home()))
    return Path(text)


def _candidate_env_files(repo_root: str | Path, config: dict[str, Any]) -> list[Path]:
    raw_paths: list[str] = []
    for key in ("IBKR_ENV_FILE", "HFT3_IBKR_ENV_FILE", "QXL_KEYS_ENV"):
        if os.environ.get(key):
            raw_paths.append(os.environ[key])
    raw_paths.extend(str(path) for path in (config.get("env_files") or []))
    raw_paths.extend(
        [
            "C:/QuantX/keys.env",
            "C:/QuantX/.env",
            "%USERPROFILE%/.config/quant-x/keys.env",
            "%USERPROFILE%/.config/quant-x/.env",
            "%USERPROFILE%/Documents/GitHub/quant-x/.env.local",
            "%USERPROFILE%/Documents/GitHub/quant-x/.env",
        ]
    )
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = _expand_local_path(raw)
        if not path.is_absolute():
            path = Path(repo_root) / path
        key = str(path).lower()
        if key not in seen:
            paths.append(path)
            seen.add(key)
    return paths


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key in ALLOWED_ENV_KEYS or any(key.startswith(prefix) for prefix in ALLOWED_ENV_PREFIXES):
            if value and value.lower() not in {"replace-me", "changeme", "none", "null"}:
                values[key] = value
    return values


def hydrate_runtime_env(repo_root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Load local runtime env values without exposing their contents.

    ``loaded_key_count`` reflects only keys actually read from env files on
    disk, not built-in defaults applied when a key is absent.  A file
    containing four keys produces a count of four, regardless of how many
    defaults are subsequently applied.
    """

    loaded_paths: list[str] = []
    # Keys sourced from files (determines loaded_key_count).
    file_loaded_keys: set[str] = set()
    for path in _candidate_env_files(repo_root, config):
        if not path.is_file():
            continue
        values = _parse_env_file(path)
        applied = False
        for key, value in values.items():
            if not os.environ.get(key):
                os.environ[key] = value
                file_loaded_keys.add(key)
                applied = True
        if applied:
            loaded_paths.append(str(path))

    # Apply built-in defaults for keys not yet set; these do not count toward
    # loaded_key_count because they were not read from any file.
    defaults = {
        "BROKER_SOCKET_ENABLED": "1",
        "MARKET_DATA_PROVIDER": "ibkr-socket",
        "IBKR_SOCKET_HOST": "127.0.0.1",
        "IBKR_SOCKET_MODE": str(config.get("mode") or "paper"),
        "IBKR_SOCKET_CLIENT_ID_PRIMARY": str((config.get("client_ids") or {}).get("broker_primary") or 17),
        "IBKR_SOCKET_CLIENT_ID_MARKETDATA": str((config.get("client_ids") or {}).get("market_data") or 18),
    }
    for key, value in defaults.items():
        if not os.environ.get(key):
            os.environ[key] = value
    if not os.environ.get("IBKR_ACCOUNT_ID") and os.environ.get("IBKR_ACCOUNT_ID_PRIMARY"):
        os.environ["IBKR_ACCOUNT_ID"] = os.environ["IBKR_ACCOUNT_ID_PRIMARY"]

    return {
        "loaded_paths": loaded_paths,
        "loaded_key_count": len(file_loaded_keys),
        "loaded_keys": sorted(file_loaded_keys),
        "redacted": True,
    }


def _env_name(config: dict[str, Any], key: str, fallback: str) -> str:
    env = config.get("env") or {}
    value = env.get(key) if isinstance(env, dict) else None
    return str(value or fallback)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _unique_ints(values: list[Any]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            out.append(number)
            seen.add(number)
    return out


def _candidate_ports(config: dict[str, Any], mode: str, configured_port: int) -> list[int]:
    candidates: list[Any] = []
    if os.environ.get("IBKR_SOCKET_PORT"):
        candidates.append(os.environ["IBKR_SOCKET_PORT"])
    candidates.append(configured_port)
    by_mode = config.get("candidate_ports") or {}
    if isinstance(by_mode, dict):
        candidates.extend(by_mode.get(mode) or [])
    ports = config.get("ports") or {}
    if mode == "live":
        candidates.extend([ports.get("live"), ports.get("legacy_gateway_live")])
    else:
        candidates.extend([ports.get("paper"), ports.get("legacy_gateway_paper")])
    return _unique_ints(candidates)


def _socket_reachable(host: str, port: int, timeout_sec: float) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=max(float(timeout_sec), 0.05)):
            return True
    except OSError:
        return False


def _launcher_candidates(config: dict[str, Any]) -> list[Path]:
    raw_launchers = config.get("launchers") or {}
    raw_paths: list[str] = []
    if os.environ.get("IBKR_GATEWAY_EXE"):
        raw_paths.append(os.environ["IBKR_GATEWAY_EXE"])
    if isinstance(raw_launchers, dict):
        raw_paths.extend(str(path) for path in (raw_launchers.get("ib_gateway") or []))
        raw_paths.extend(str(path) for path in (raw_launchers.get("tws") or []))
    raw_paths.extend(["C:/Jts/ibgateway/latest/ibgateway1.exe", "C:/Jts/1030/tws.exe"])
    out: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = _expand_local_path(raw)
        key = str(path).lower()
        if key not in seen:
            out.append(path)
            seen.add(key)
    return out


def _find_launcher(config: dict[str, Any]) -> Path | None:
    for path in _launcher_candidates(config):
        if path.is_file():
            return path
    return None


def _launch_gateway(config: dict[str, Any]) -> dict[str, Any]:
    launcher = _find_launcher(config)
    if launcher is None:
        return {
            "present": False,
            "action": "missing",
            "path": "",
        }
    try:
        subprocess.Popen(
            [str(launcher)],
            cwd=str(launcher.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return {
            "present": True,
            "action": "started",
            "path": str(launcher),
        }
    except OSError as exc:
        return {
            "present": True,
            "action": "start_failed",
            "path": str(launcher),
            "error": str(exc),
        }


def _probe_candidate_ports(
    host: str,
    ports: list[int],
    timeout_sec: float,
    probe: SocketProbe,
) -> tuple[bool, int | None, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    first_open: int | None = None
    for port in ports:
        reachable = bool(probe(host, port, timeout_sec))
        rows.append({"host": host, "port": port, "reachable": reachable})
        if reachable and first_open is None:
            first_open = port
    return first_open is not None, first_open, rows


@dataclass(frozen=True)
class ResolvedIbkrEndpoint:
    profile: str
    provider: str
    transport: str
    mode: str
    system: str
    gateway: str
    host: str
    port: int
    client_id: int
    market_data_client_id: int
    account_present: bool
    account_env: str
    broker_socket_enabled: bool
    market_data_provider: str
    config_path: str
    candidate_ports: list[int] = field(default_factory=list)
    env_hydration: dict[str, Any] = field(default_factory=dict)

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "provider": self.provider,
            "transport": self.transport,
            "mode": self.mode,
            "system": self.system,
            "gateway": self.gateway,
            "host": self.host,
            "port": self.port,
            "candidate_ports": list(self.candidate_ports),
            "client_id": self.client_id,
            "market_data_client_id": self.market_data_client_id,
            "credentials": {
                "account_id_set": self.account_present,
                "account_env": self.account_env,
                "redacted": True,
            },
            "broker_socket_enabled": self.broker_socket_enabled,
            "market_data_provider": self.market_data_provider,
            "config_path": self.config_path,
            "env_hydration": self.env_hydration,
            "secret_exposed": False,
        }


def resolve_endpoint(
    repo_root: str | Path,
    config_path: str | Path | None = None,
) -> ResolvedIbkrEndpoint:
    path = _resolve_config_path(repo_root, config_path)
    config = load_endpoint_config(path)
    env_hydration = hydrate_runtime_env(repo_root, config)
    ports = config.get("ports") or {}
    client_ids = config.get("client_ids") or {}

    mode_name = _env_name(config, "mode", "IBKR_SOCKET_MODE")
    mode = os.environ.get(mode_name, str(config.get("mode") or "paper")).strip().lower() or "paper"
    default_port = int(ports.get("live" if mode == "live" else "paper") or 7497)
    port_env = _env_name(config, "live_port" if mode == "live" else "paper_port", "IBKR_SOCKET_PORT_PAPER")
    host_env = _env_name(config, "host", "IBKR_SOCKET_HOST")
    broker_client_env = _env_name(config, "broker_client_id", "IBKR_SOCKET_CLIENT_ID_PRIMARY")
    market_client_env = _env_name(config, "market_data_client_id", "IBKR_SOCKET_CLIENT_ID_MARKETDATA")
    account_primary_env = _env_name(config, "account_primary", "IBKR_ACCOUNT_ID_PRIMARY")
    account_fallback_env = _env_name(config, "account_fallback", "IBKR_ACCOUNT_ID")
    socket_enabled_env = _env_name(config, "broker_socket_enabled", "BROKER_SOCKET_ENABLED")
    provider_env = _env_name(config, "market_data_provider", "MARKET_DATA_PROVIDER")
    account_env = account_primary_env if os.environ.get(account_primary_env) else account_fallback_env
    configured_port = _env_int(port_env, default_port)
    candidates = _candidate_ports(config, mode, configured_port)

    return ResolvedIbkrEndpoint(
        profile=str(config.get("profile") or "ibkr_paper_socket"),
        provider=str(config.get("provider") or "interactive_brokers"),
        transport=str(config.get("transport") or "tws_socket"),
        mode=mode,
        system=str(config.get("system") or "IBKR Paper Trading"),
        gateway=str(config.get("gateway") or "TWS or IB Gateway headless socket"),
        host=os.environ.get(host_env, str(config.get("host") or "127.0.0.1")).strip() or "127.0.0.1",
        port=configured_port,
        client_id=_env_int(broker_client_env, int(client_ids.get("broker_primary") or 17)),
        market_data_client_id=_env_int(market_client_env, int(client_ids.get("market_data") or 18)),
        account_present=bool(os.environ.get(account_primary_env) or os.environ.get(account_fallback_env)),
        account_env=account_env,
        broker_socket_enabled=_env_bool(socket_enabled_env, default=False),
        market_data_provider=os.environ.get(provider_env, "").strip(),
        config_path=str(path),
        candidate_ports=candidates,
        env_hydration=env_hydration,
    )


def _ibapi_available() -> bool:
    return importlib.util.find_spec("ibapi") is not None


def _headless_ibapi_handshake(endpoint: ResolvedIbkrEndpoint, timeout_sec: float) -> dict[str, Any]:
    if not _ibapi_available():
        return {
            "api_client_status": "IBAPI_PACKAGE_MISSING",
            "api_package_present": False,
            "connected": False,
        }

    # Import lazily so normal Workbench rendering does not require ibapi.
    from ibapi.client import EClient  # type: ignore
    from ibapi.wrapper import EWrapper  # type: ignore

    ready = threading.Event()
    errors: list[dict[str, Any]] = []

    class ProbeClient(EWrapper, EClient):  # type: ignore[misc]
        def __init__(self) -> None:
            EClient.__init__(self, self)
            self.next_valid_order_id: int | None = None
            self.managed_accounts: list[str] = []

        def nextValidId(self, orderId: int) -> None:  # noqa: N802 - IB API callback name
            self.next_valid_order_id = int(orderId)
            ready.set()

        def managedAccounts(self, accountsList: str) -> None:  # noqa: N802 - IB API callback name
            self.managed_accounts = [item.strip() for item in str(accountsList or "").split(",") if item.strip()]

        def error(self, reqId: int, errorCode: int, errorString: str, *args: Any) -> None:  # noqa: N802
            errors.append(
                {
                    "req_id": reqId,
                    "code": errorCode,
                    "message": str(errorString),
                }
            )

    client = ProbeClient()
    thread: threading.Thread | None = None
    try:
        client.connect(endpoint.host, int(endpoint.port), int(endpoint.client_id))
        thread = threading.Thread(target=client.run, name="ibkr-endpoint-probe", daemon=True)
        thread.start()
        handshake_ready = ready.wait(timeout=max(float(timeout_sec), 0.25))
        if handshake_ready:
            status = "CONNECTED"
        elif any(int(error.get("code") or 0) == 10141 for error in errors):
            status = "PAPER_DISCLAIMER_PENDING"
        else:
            status = "IBAPI_HANDSHAKE_TIMEOUT"
        return {
            "api_client_status": status,
            "api_package_present": True,
            "connected": bool(handshake_ready),
            "next_valid_order_id_observed": bool(client.next_valid_order_id is not None),
            "managed_accounts_observed": bool(client.managed_accounts),
            "errors": errors[-5:],
        }
    except Exception as exc:  # pragma: no cover - exercised only with a real gateway failure
        return {
            "api_client_status": "IBAPI_CONNECT_ERROR",
            "api_package_present": True,
            "connected": False,
            "error": str(exc),
            "errors": errors[-5:],
        }
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        if thread and thread.is_alive():
            thread.join(timeout=0.25)


def endpoint_status(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    connect: bool = False,
    start_gateway: bool = False,
    startup_timeout_sec: float = 20.0,
    timeout_sec: float = 1.0,
    socket_probe: SocketProbe | None = None,
    write_status: bool = True,
) -> dict[str, Any]:
    cfg_path = _resolve_config_path(repo_root, config_path)
    config = load_endpoint_config(cfg_path)
    endpoint = resolve_endpoint(repo_root, config_path)
    probe = socket_probe or _socket_reachable
    socket_open, open_port, port_rows = _probe_candidate_ports(
        endpoint.host,
        endpoint.candidate_ports or [endpoint.port],
        timeout_sec,
        probe,
    )
    launch_status = {
        "present": _find_launcher(config) is not None,
        "action": "not_requested",
        "path": str(_find_launcher(config) or ""),
    }
    if start_gateway and not socket_open:
        launch_status = _launch_gateway(config)
        deadline = time.monotonic() + max(float(startup_timeout_sec), 0.0)
        while time.monotonic() < deadline:
            socket_open, open_port, port_rows = _probe_candidate_ports(
                endpoint.host,
                endpoint.candidate_ports or [endpoint.port],
                timeout_sec,
                probe,
            )
            if socket_open:
                break
            time.sleep(0.5)
    effective_port = int(open_port or endpoint.port)
    endpoint_for_connect = replace(endpoint, port=effective_port)
    api_available = _ibapi_available()
    blockers: list[dict[str, Any]] = []
    runtime_policy: list[dict[str, Any]] = []
    if not endpoint.broker_socket_enabled:
        runtime_policy.append(
            {
                "gate": "ibkr_broker_socket_enabled",
                "status": "WARN",
                "reason": "BROKER_SOCKET_ENABLED is not enabled for the runtime.",
            }
        )
    if endpoint.market_data_provider and endpoint.market_data_provider != "ibkr-socket":
        runtime_policy.append(
            {
                "gate": "ibkr_market_data_provider",
                "status": "WARN",
                "reason": "MARKET_DATA_PROVIDER is not ibkr-socket.",
            }
        )
    routing_gates: list[dict[str, Any]] = []
    if not endpoint.account_present:
        routing_gates.append(
            {
                "gate": "ibkr_account",
                "status": "WARN",
                "reason": f"{endpoint.account_env} is not loaded in the process environment.",
            }
        )
    if not socket_open:
        blockers.append(
            {
                "gate": "ibkr_socket",
                "status": "BLOCKING",
                "reason": (
                    f"TWS/IB Gateway socket is not reachable at {endpoint.host} on candidate ports "
                    f"{', '.join(map(str, endpoint.candidate_ports or [endpoint.port]))}."
                ),
            }
        )
    if not api_available:
        blockers.append(
            {
                "gate": "ibkr_api_package",
                "status": "BLOCKING",
                "reason": "Python package ibapi is not installed; headless API handshake cannot run.",
            }
        )

    api_handshake: dict[str, Any] = {
        "api_client_status": "NOT_REQUESTED",
        "api_package_present": api_available,
        "connected": False,
        "headless_required": bool(connect),
    }
    if connect and socket_open and api_available:
        api_handshake = _headless_ibapi_handshake(endpoint_for_connect, timeout_sec)
        if not api_handshake.get("connected"):
            api_status = str(api_handshake.get("api_client_status") or "IBAPI_HANDSHAKE_FAILED")
            if api_status == "PAPER_DISCLAIMER_PENDING":
                gate = "ibkr_paper_disclaimer"
                reason = "Paper trading disclaimer must be accepted before IBKR allows API connections."
            else:
                gate = "ibkr_api_handshake"
                reason = api_status
            blockers.append(
                {
                    "gate": gate,
                    "status": "BLOCKING",
                    "reason": reason,
                }
            )
    elif connect and socket_open and not api_available:
        api_handshake["api_client_status"] = "IBAPI_PACKAGE_MISSING"

    if connect:
        status = "CONNECTED" if api_handshake.get("connected") and not blockers else "BLOCKING"
    else:
        status = "READY_TO_CONNECT" if socket_open and api_available and not blockers else "BLOCKING"

    reason_code = ""
    if blockers:
        reason_code = str(blockers[0].get("gate") or "IBKR_ENDPOINT_BLOCKED").upper()
    payload = {
        "schema_version": "ibkr_endpoint_status_v1",
        "generated_at_utc": utc_now(),
        "status": status,
        "reason_code": reason_code,
        "headless_handshake_required": bool(connect),
        **endpoint_for_connect.to_redacted_dict(),
        "socket": {
            "host": endpoint.host,
            "port": effective_port,
            "reachable": socket_open,
            "timeout_sec": timeout_sec,
            "candidate_ports": endpoint.candidate_ports or [endpoint.port],
            "port_checks": port_rows,
        },
        "api": api_handshake,
        "blocking_gates": blockers,
        "routing_gates": routing_gates,
        "runtime_policy": runtime_policy,
        "launcher": launch_status,
        "quantx_reference": {
            # Prefer an explicit env override; fall back to the conventional
            # sibling-directory layout (../quant-x relative to this repo).
            "repo": os.environ.get(
                "QUANTX_REPO",
                str(Path(__file__).resolve().parents[4].parent / "quant-x"),
            ),
            "runbook": "docs/ibkr_weekly_login.md",
            "default_socket_provider": "ibkr-socket",
            "paper_port": 7497,
            "live_port": 7496,
            "broker_client_id": 17,
            "market_data_client_id": 18,
        },
    }
    if write_status:
        status_path = default_status_path(repo_root)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["runtime_status_path"] = str(status_path)
    else:
        payload["runtime_status_path"] = ""
    return payload
