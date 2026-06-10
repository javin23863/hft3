"""Interactive Brokers endpoint readiness for the equities lane — Web API edition.

The endpoint shape uses the IBKR Client Portal REST API (Web API) exclusively.
TWS/IB Gateway socket paths (ports 7497/4002) are retired; no desktop GUI required.

Two auth modes:
  oauth   — OAuth 1.0a direct to https://api.ibkr.com/v1/api (fully unattended).
  gateway — clientportal.gw headless Java gateway at https://localhost:5000/v1/api;
            one-time browser login per session; POST /tickle for keepalive.

Default auth: oauth (from config ``default_auth``; overridable via env
``IBKR_AUTH_MODE`` or explicit ``auth_mode`` argument).

Status artifact at runtime/equities_lane/ibkr_endpoint_status.json includes the
top-level ``"ready"`` boolean read by downstream agents.

No network calls at import time.  No secrets in the status artifact.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_CONFIG_REL = Path("packages/equities_lane/config/ibkr_endpoint.yaml")
DEFAULT_STATUS_REL = Path("runtime/equities_lane/ibkr_endpoint_status.json")

ALLOWED_ENV_PREFIXES = ("IBKR_",)
ALLOWED_ENV_KEYS = {
    "BROKER_SOCKET_ENABLED",
    "MARKET_DATA_PROVIDER",
}

# Callable types for injected transport (enables mocking in tests).
HttpGet = Callable[[str], Any]
HttpPost = Callable[[str], Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_config_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / DEFAULT_CONFIG_REL


def default_status_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / DEFAULT_STATUS_REL


def load_endpoint_config(path: str | Path) -> dict[str, Any]:
    import yaml  # local import keeps module importable without pyyaml at parse time

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

    return {
        "loaded_paths": loaded_paths,
        "loaded_key_count": len(file_loaded_keys),
        "loaded_keys": sorted(file_loaded_keys),
        "redacted": True,
    }


def _resolve_auth_mode(config: dict[str, Any], auth_mode: str | None) -> str:
    """Resolve effective auth mode: explicit arg > env > config default > 'oauth'."""
    if auth_mode:
        return str(auth_mode).strip().lower()
    env_val = os.environ.get("IBKR_AUTH_MODE", "").strip().lower()
    if env_val:
        return env_val
    return str(config.get("default_auth") or "oauth").strip().lower()


def _base_url(config: dict[str, Any], auth_mode: str) -> str:
    """Return the base URL for the given auth mode."""
    block = config.get(auth_mode) or {}
    if isinstance(block, dict) and block.get("base_url"):
        return str(block["base_url"]).rstrip("/")
    # Sensible fallbacks if config block is absent.
    if auth_mode == "gateway":
        return "https://localhost:5000/v1/api"
    return "https://api.ibkr.com/v1/api"


def _paper_account_id(config: dict[str, Any]) -> str | None:
    """Return the paper account id from the environment (never from config).

    Only the env var named by ``paper_account_env`` (default
    ``IBKR_ACCOUNT_ID_PAPER``) is consulted.  Legacy keys such as
    ``IBKR_ACCOUNT_ID_PRIMARY`` or ``IBKR_ACCOUNT_ID`` are intentionally
    ignored to prevent a live account from being mistaken for a paper account.
    """
    env_key = str(config.get("paper_account_env") or "IBKR_ACCOUNT_ID_PAPER")
    val = os.environ.get(env_key, "").strip()
    return val if val else None


# ---------------------------------------------------------------------------
# REST probe helpers — all take an injected http_get/http_post so that tests
# can mock without any real network.
# ---------------------------------------------------------------------------


def _probe_auth_status(http_get: HttpGet, base_url: str) -> dict[str, Any]:
    """GET /iserver/auth/status — returns {ok: bool, wall_time_ms: float, raw: dict}."""
    try:
        t0 = time.monotonic()
        response = http_get(f"{base_url}/iserver/auth/status")
        wall_time_ms = (time.monotonic() - t0) * 1000.0
        authenticated = bool(
            (response or {}).get("authenticated")
            if isinstance(response, dict)
            else False
        )
        return {"ok": authenticated, "wall_time_ms": wall_time_ms, "raw": response}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "raw": None}


def _probe_tickle(http_post: HttpPost, base_url: str) -> dict[str, Any]:
    """POST /tickle — returns {ok: bool, wall_time_ms: float, raw: dict}."""
    try:
        t0 = time.monotonic()
        response = http_post(f"{base_url}/tickle")
        wall_time_ms = (time.monotonic() - t0) * 1000.0
        ok = response is not None and not (isinstance(response, dict) and response.get("error"))
        return {"ok": ok, "wall_time_ms": wall_time_ms, "raw": response}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "raw": None}


def _probe_accounts(
    http_get: HttpGet,
    base_url: str,
    paper_account_id: str | None,
) -> dict[str, Any]:
    """GET /iserver/accounts — returns {ok: bool, wall_time_ms: float, paper_account_present: bool, raw: list}."""
    try:
        t0 = time.monotonic()
        response = http_get(f"{base_url}/iserver/accounts")
        wall_time_ms = (time.monotonic() - t0) * 1000.0
        accounts: list[str] = []
        if isinstance(response, dict):
            accounts = list(response.get("accounts") or [])
        elif isinstance(response, list):
            accounts = [str(a) for a in response]
        paper_present = bool(paper_account_id and paper_account_id in accounts)
        return {
            "ok": bool(accounts),
            "wall_time_ms": wall_time_ms,
            "paper_account_present": paper_present,
            "account_count": len(accounts),
            "raw": response,
        }
    except Exception as exc:
        return {
            "ok": False,
            "paper_account_present": False,
            "account_count": 0,
            "error": str(exc),
            "raw": None,
        }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedIbkrEndpoint:
    """Web-API endpoint config resolved from yaml + environment."""

    provider: str
    transport: str
    mode: str
    auth_mode: str
    base_url: str
    paper_account_env: str
    paper_account_present: bool
    config_path: str
    env_hydration: dict[str, Any] = field(default_factory=dict)

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "transport": self.transport,
            "mode": self.mode,
            "auth_mode": self.auth_mode,
            "base_url": self.base_url,
            "credentials": {
                "paper_account_env": self.paper_account_env,
                "account_id_set": self.paper_account_present,
                "redacted": True,
            },
            "config_path": self.config_path,
            "env_hydration": self.env_hydration,
            "secret_exposed": False,
        }


def resolve_endpoint(
    repo_root: str | Path,
    config_path: str | Path | None = None,
    auth_mode: str | None = None,
) -> ResolvedIbkrEndpoint:
    path = _resolve_config_path(repo_root, config_path)
    config = load_endpoint_config(path)
    env_hydration = hydrate_runtime_env(repo_root, config)

    effective_auth_mode = _resolve_auth_mode(config, auth_mode)
    url = _base_url(config, effective_auth_mode)
    paper_id = _paper_account_id(config)
    paper_env_key = str(config.get("paper_account_env") or "IBKR_ACCOUNT_ID_PAPER")

    return ResolvedIbkrEndpoint(
        provider=str(config.get("provider") or "interactive_brokers"),
        transport=str(config.get("transport") or "web_api"),
        mode=str(config.get("mode") or "paper"),
        auth_mode=effective_auth_mode,
        base_url=url,
        paper_account_env=paper_env_key,
        paper_account_present=bool(paper_id),
        config_path=str(path),
        env_hydration=env_hydration,
    )


def endpoint_status(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    auth_mode: str | None = None,
    http_get: HttpGet | None = None,
    http_post: HttpPost | None = None,
    write_status: bool = True,
    # Deprecated v1 kwargs — accepted and silently ignored for backward compat
    # with callers that have not yet been updated (workbench __main__).
    connect: bool = False,
    start_gateway: bool = False,
    startup_timeout_sec: float = 20.0,
    timeout_sec: float = 1.0,
    socket_probe: Any = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Run Web API readiness probes and write the status artifact.

    Injected ``http_get`` / ``http_post`` callables allow full mocking in tests.
    When not provided the function builds a real requests.Session (lazy import
    so the module stays importable without requests installed).

    Status artifact contract (schema_version: ibkr_endpoint_status_v2):

        {
          "ready": <bool>,            # True when auth ok + accounts visible + paper account matched
          "schema_version": "ibkr_endpoint_status_v2",
          "transport": "web_api",
          "auth_mode": "<oauth|gateway>",
          "checked_at_utc": "<ISO-8601>",
          "measured_rtt_ms": <float|null>,  # median probe wall-time in ms (null if no probe completed)
          "probes": {
            "auth_status": {"ok": <bool>, "wall_time_ms": <float>, ...},
            "tickle":       {"ok": <bool>, "wall_time_ms": <float>, ...},
            "accounts":     {"ok": <bool>, "wall_time_ms": <float>, "paper_account_present": <bool>, ...}
          },
          "paper_account_present": <bool>,
          "provider": "interactive_brokers",
          "mode": "paper",
          "base_url": "...",
          "credentials": {"account_id_set": <bool>, "redacted": true},
          "secret_exposed": false
        }
    """
    cfg_path = _resolve_config_path(repo_root, config_path)
    config = load_endpoint_config(cfg_path)
    endpoint = resolve_endpoint(repo_root, config_path, auth_mode=auth_mode)

    # Build real transport only if not injected (lazy — no network at import time).
    _get: HttpGet
    _post: HttpPost
    if http_get is not None and http_post is not None:
        _get = http_get
        _post = http_post
    else:
        _get, _post = _build_real_transport(endpoint)

    base = endpoint.base_url
    probe_auth = _probe_auth_status(_get, base)
    probe_tick = _probe_tickle(_post, base)
    paper_id = _paper_account_id(config)
    probe_acct = _probe_accounts(_get, base, paper_id)

    auth_ok = bool(probe_auth.get("ok"))
    tickle_ok = bool(probe_tick.get("ok"))
    accounts_ok = bool(probe_acct.get("ok"))
    paper_present = bool(probe_acct.get("paper_account_present"))

    ready = auth_ok and accounts_ok and paper_present

    # Compute median RTT from completed probe wall-times (null when none completed).
    _probe_times = [
        p["wall_time_ms"]
        for p in (probe_auth, probe_tick, probe_acct)
        if isinstance(p.get("wall_time_ms"), (int, float))
    ]
    if _probe_times:
        _sorted = sorted(_probe_times)
        _mid = len(_sorted) // 2
        _median = _sorted[_mid] if len(_sorted) % 2 else (_sorted[_mid - 1] + _sorted[_mid]) / 2.0
        measured_rtt_ms: float | None = round(_median, 1)
    else:
        measured_rtt_ms = None

    payload: dict[str, Any] = {
        "ready": ready,
        "schema_version": "ibkr_endpoint_status_v2",
        "transport": "web_api",
        "auth_mode": endpoint.auth_mode,
        "checked_at_utc": utc_now(),
        "measured_rtt_ms": measured_rtt_ms,
        "probes": {
            "auth_status": {k: v for k, v in probe_auth.items() if k != "raw"},
            "tickle": {k: v for k, v in probe_tick.items() if k != "raw"},
            "accounts": {k: v for k, v in probe_acct.items() if k != "raw"},
        },
        "paper_account_present": paper_present,
        **endpoint.to_redacted_dict(),
    }

    if write_status:
        status_path = default_status_path(repo_root)
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        payload["runtime_status_path"] = str(status_path)
    else:
        payload["runtime_status_path"] = ""

    return payload


def _build_real_transport(endpoint: ResolvedIbkrEndpoint) -> tuple[HttpGet, HttpPost]:
    """Build a requests.Session-backed transport.  Called only at runtime, not at import."""
    import requests  # type: ignore

    session = requests.Session()
    verify: bool | str = True
    if "localhost" in endpoint.base_url or "127.0.0.1" in endpoint.base_url:
        verify = False

    def _get(url: str) -> Any:
        resp = session.get(url, verify=verify, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(url: str) -> Any:
        resp = session.post(url, verify=verify, timeout=10)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code}

    return _get, _post
