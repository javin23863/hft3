from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


EXTERNAL_ENDPOINT_PROFILE = "external_chicago"
EXTERNAL_ENDPOINT_MISSING_CODE = "EXTERNAL_ENDPOINT_PARAMS_MISSING"

REQUIRED_ENVIRONMENT_PARAMS = (
    "MML_DMN_SRVR_ADDR",
    "MML_DOMAIN_NAME",
    "MML_LIC_SRVR_ADDR",
    "MML_LOC_BROK_ADDR",
    "MML_LOGGER_ADDR",
)

REQUIRED_REPOSITORY_PARAMS = ("sCnnctPt",)
REQUIRED_LOGIN_PARAMS = (
    "sMdCnnctPt",
    "sTsCnnctPt",
    "sIhCnnctPt",
    "sPnlCnnctPt",
)


def load_api_yaml(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def default_api_config_for_profile(repo_root: Path, profile: str) -> Path:
    name = "rithmic_api_external.yaml" if profile == EXTERNAL_ENDPOINT_PROFILE else "rithmic_api_test.yaml"
    return repo_root / "packages" / "data_system" / "config" / name


def _missing_keys(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if not str(payload.get(key) or "").strip()]


def _runtime_status_path(repo_root: Path) -> Path:
    return repo_root / "runtime" / "rithmic_trial" / "rithmic_endpoint_status.json"


def _default_gateway_library_path(repo_root: Path) -> Path:
    if sys.platform.startswith("win"):
        candidates = [
            repo_root / "build-msvc" / "rithmic_gateway" / "rithmic_gateway_shared.dll",
            repo_root / "build" / "rithmic_gateway" / "rithmic_gateway_shared.dll",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]
    return repo_root / "build" / "rithmic_gateway" / "librithmic_gateway_shared.so"


def endpoint_status_from_config(
    config_path: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(config_path)
    root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
    config_exists = path.is_file()
    cfg = load_api_yaml(path)
    profile = str(os.environ.get("RITHMIC_ENDPOINT_PROFILE") or cfg.get("endpoint_profile") or "").strip()
    if not profile:
        profile = "test_orangeburg" if str(cfg.get("system") or "").lower().startswith("rithmic test") else "unknown"

    env_block = cfg.get("environment") or {}
    repository_login = cfg.get("repository_login") or {}
    login_params = cfg.get("login_params") or {}
    required_env = tuple(cfg.get("required_environment_params") or REQUIRED_ENVIRONMENT_PARAMS)
    required_repo = tuple(cfg.get("required_repository_params") or REQUIRED_REPOSITORY_PARAMS)
    required_login = tuple(cfg.get("required_login_params") or REQUIRED_LOGIN_PARAMS)
    missing_endpoint_params = (
        [f"environment.{key}" for key in _missing_keys(env_block, required_env)]
        + [f"repository_login.{key}" for key in _missing_keys(repository_login, required_repo)]
        + [f"login_params.{key}" for key in _missing_keys(login_params, required_login)]
    )

    username_set = bool(os.environ.get("RITHMIC_USERNAME"))
    password_set = bool(os.environ.get("RITHMIC_PASSWORD"))
    gateway_so = os.environ.get("HFT3_RITHMIC_GATEWAY_SO") or str(_default_gateway_library_path(root))

    reason_code = ""
    status = "READY_TO_CONNECT"
    if not config_exists:
        status = "BLOCKED"
        reason_code = "RITHMIC_API_CONFIG_MISSING"
    elif profile == EXTERNAL_ENDPOINT_PROFILE and missing_endpoint_params:
        status = "BLOCKED"
        reason_code = EXTERNAL_ENDPOINT_MISSING_CODE
    elif not username_set or not password_set:
        status = "CONFIGURED_NOT_AUTHENTICATED"
        reason_code = "RITHMIC_CREDENTIALS_MISSING"
    elif not Path(gateway_so).is_file():
        status = "CONFIGURED_NOT_OBSERVED"
        reason_code = "GATEWAY_LIBRARY_NOT_FOUND"

    display_system = "Rithmic external broker" if profile == EXTERNAL_ENDPOINT_PROFILE else str(cfg.get("system") or "")

    return {
        "status": status,
        "reason_code": reason_code,
        "profile": profile,
        "system": display_system,
        "gateway": str(cfg.get("gateway") or ""),
        "config_path": str(path),
        "config_exists": config_exists,
        "credentials": {
            "username_set": username_set,
            "password_set": password_set,
            "redacted": True,
        },
        "gateway_library": {
            "path": gateway_so,
            "exists": Path(gateway_so).is_file(),
        },
        "missing_endpoint_params": missing_endpoint_params,
        "last_connection_attempt": "",
        "runtime_status_path": str(_runtime_status_path(root)),
        "secret_exposed": False,
    }


def write_endpoint_status(repo_root: str | Path, status: dict[str, Any]) -> Path:
    root = Path(repo_root).resolve()
    path = _runtime_status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(status)
    payload["last_connection_attempt"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
