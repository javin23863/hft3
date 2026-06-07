"""Shared desk keyring discovery for hft3 (QuantX / crypto-alpha-engine siblings)."""
from __future__ import annotations

import os
from pathlib import Path

_SIBLING_ENVS: tuple[tuple[str, str], ...] = (
    ("quant-x", "../quant-x"),
    ("crypto-alpha-engine", "../crypto-alpha-engine"),
)

_DESK_KEY_CANDIDATES: tuple[str, ...] = (
    "CRYPTO_KEYS_ENV",
    "MACRO_KEYS_ENV",
    "QXL_KEYS_ENV",
)


def load_plain_env(path: Path, *, override: bool = False) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
    return True


def _sibling_env(repo_root: Path, relpath: str) -> Path | None:
    candidate = (repo_root / relpath).resolve() / ".env"
    return candidate if candidate.is_file() else None


def load_sibling_pointer_envs(repo_root: Path) -> list[Path]:
    """Load quant-x / crypto-alpha-engine .env for QXL_KEYS_ENV and CAE_B2_* pointers."""
    loaded: list[Path] = []
    for _name, relpath in _SIBLING_ENVS:
        path = _sibling_env(repo_root, relpath)
        if path and load_plain_env(path, override=False):
            loaded.append(path)
    return loaded


def resolve_desk_keys_path() -> Path | None:
    """Resolve master keys.env using the same precedence as quant-x load-private-env.js."""
    for env_name in _DESK_KEY_CANDIDATES:
        explicit = os.environ.get(env_name, "").strip()
        if explicit:
            path = Path(explicit)
            if path.is_file():
                return path

    windows_default = Path(r"C:\QuantX\keys.env")
    if windows_default.is_file():
        return windows_default

    desk = Path.home() / "Desktop" / "keys.env"
    if desk.is_file():
        return desk

    quantx_desk = Path.home() / "Desktop" / "quant-x-env" / "keys.env"
    if quantx_desk.is_file():
        return quantx_desk

    return None


def ensure_desk_env(repo_root: Path) -> list[Path]:
    """Load sibling repo pointers then desk keys.env; return paths loaded."""
    loaded = load_sibling_pointer_envs(repo_root)
    keys_path = resolve_desk_keys_path()
    if keys_path and load_plain_env(keys_path, override=False):
        if keys_path not in loaded:
            loaded.append(keys_path)
    return loaded
