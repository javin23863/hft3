"""Load macro API keys from hft3 .env and desk keyring (Desktop/keys.env)."""

from __future__ import annotations

import os
from pathlib import Path

from hft3_bootstrap import repo_root

_LOADED = False


def _load_plain_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_macro_env() -> None:
    """Load repo .env, QuantX sibling pointers, then desk keys.env."""
    global _LOADED
    if _LOADED:
        return

    root = repo_root()
    dotenv_path = root / ".env"
    if dotenv_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path, override=False)
        except ImportError:
            _load_plain_env(dotenv_path)

    import desk_env

    for path in desk_env.load_sibling_pointer_envs(root):
        _load_plain_env(path)

    keys_path = desk_env.resolve_desk_keys_path()
    if keys_path:
        _load_plain_env(keys_path)

    _LOADED = True


def fred_api_key() -> str:
    ensure_macro_env()
    return os.getenv("FRED_API_KEY", "").strip()
