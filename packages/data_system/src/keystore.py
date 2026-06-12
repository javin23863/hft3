"""Single-source credential loader for the data system.

All API keys live in ONE master file (default: the user's Desktop ``keys.env``),
so credentials are not scattered across per-package ``.env`` files.  Resolution
order, first found wins per variable (values already in the environment are
never overwritten):

    1. ``os.environ``                      (already-exported vars win)
    2. ``$HFT3_KEYS_ENV``                   (explicit override path)
    3. ``~/Desktop/keys.env``              (the master store)
    4. ``<repo>/.env``                     (repo-local, e.g. Rithmic creds)

The master path is overridable with the ``HFT3_KEYS_ENV`` environment variable
so the same loader works on machines where the master lives elsewhere (e.g. the
colo box).  Credential VALUES are never logged — only names/status.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

_LOADED: List[Path] = []


def _repo_root() -> Path:
    # packages/data_system/src/keystore.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def master_keys_path() -> Path:
    """Path to the single master keys file."""
    override = os.environ.get("HFT3_KEYS_ENV", "").strip()
    if override:
        return Path(override)
    return Path.home() / "Desktop" / "keys.env"


def candidate_paths() -> List[Path]:
    return [master_keys_path(), _repo_root() / ".env"]


def _load_plain_env(path: Path) -> int:
    """Load KEY=VALUE lines without overwriting existing env vars. Returns count set."""
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def load_keys() -> List[Path]:
    """Load credentials from the master store (+ repo .env) once. Returns files found."""
    global _LOADED
    if _LOADED:
        return list(_LOADED)
    for path in candidate_paths():
        if path.is_file():
            _load_plain_env(path)
            if path not in _LOADED:
                _LOADED.append(path)
    return list(_LOADED)


def require(*names: str) -> dict:
    """Ensure keys are loaded and present; raise listing missing names (no values)."""
    load_keys()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        loaded = ", ".join(str(p) for p in _LOADED) or "(none)"
        raise RuntimeError(
            f"Missing credentials: {missing}. Add them to the master keys file "
            f"({master_keys_path()}). Loaded: {loaded}"
        )
    return {n: os.environ[n] for n in names}


def status(*names: str) -> dict:
    """Return {name: 'set'|'missing'} without exposing values."""
    load_keys()
    return {n: ("set" if os.environ.get(n) else "missing") for n in names}
