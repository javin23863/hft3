"""Discover and load crypto lane credentials from standard env file paths."""
from __future__ import annotations

import os
from pathlib import Path

from crypto_lane.src.types import repo_root_from_lane

_ENV_ALIASES: list[tuple[str, str]] = [
    ("HFT3_CRYPTO_B2_KEY_ID", "CAE_B2_KEY_ID"),
    ("HFT3_CRYPTO_B2_APP_KEY", "CAE_B2_APP_KEY"),
    ("HFT3_CRYPTO_B2_BUCKET", "CAE_B2_BUCKET"),
    ("HFT3_CRYPTO_B2_SOURCE_BUCKET", "CAE_B2_SOURCE_BUCKET"),
    ("HFT3_CRYPTO_B2_ENDPOINT", "CAE_B2_ENDPOINT"),
    # S3-compatible credential fallbacks (Backblaze B2 uses S3 API)
    ("HFT3_CRYPTO_B2_KEY_ID", "AWS_ACCESS_KEY_ID"),
    ("HFT3_CRYPTO_B2_APP_KEY", "AWS_SECRET_ACCESS_KEY"),
    ("HFT3_CRYPTO_B2_ENDPOINT", "B2_ENDPOINT_URL"),
    ("HFT3_CRYPTO_KRAKEN_API_KEY", "KRAKEN_API_KEY"),
    ("HFT3_CRYPTO_KRAKEN_PRIVATE_KEY", "KRAKEN_PRIVATE_KEY"),
]

_LOADED_FILES: list[Path] = []


def repo_env_paths() -> list[Path]:
    root = repo_root_from_lane()
    paths = [
        root / ".env",
        root / ".btc-node.env",
    ]
    home = Path.home() / ".btc-node.env"
    if home not in paths:
        paths.append(home)
    keys_env = os.environ.get("CRYPTO_KEYS_ENV", "").strip()
    if keys_env:
        paths.append(Path(keys_env))
    return paths


def _apply_aliases() -> None:
    for primary, fallback in _ENV_ALIASES:
        if not os.environ.get(primary) and os.environ.get(fallback):
            os.environ[primary] = os.environ[fallback]


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


def ensure_crypto_env() -> list[Path]:
    """Load env files once; return paths that were found."""
    global _LOADED_FILES
    if _LOADED_FILES:
        _apply_aliases()
        return list(_LOADED_FILES)

    root = repo_root_from_lane()
    dotenv_path = root / ".env"
    if dotenv_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path, override=False)
        except ImportError:
            _load_plain_env(dotenv_path)
        _LOADED_FILES.append(dotenv_path)

    for path in repo_env_paths():
        if path == dotenv_path:
            continue
        if path.is_file():
            _load_plain_env(path)
            if path not in _LOADED_FILES:
                _LOADED_FILES.append(path)

    _apply_aliases()
    return list(_LOADED_FILES)


def require_env(*names: str, hint: str = ".env.example") -> dict[str, str]:
    ensure_crypto_env()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        loaded = ", ".join(str(p) for p in _LOADED_FILES) or "(none)"
        raise RuntimeError(
            f"Missing env keys: {missing}. Copy {hint} and populate local gitignored files. "
            f"Loaded: {loaded}"
        )
    return {n: os.environ[n] for n in names}


def env_status(*names: str) -> dict[str, str]:
    ensure_crypto_env()
    out: dict[str, str] = {}
    for name in names:
        val = os.environ.get(name, "")
        out[name] = "set" if val else "missing"
    return out


def redacted_env_report() -> dict[str, object]:
    ensure_crypto_env()
    keys = [
        "HFT3_CRYPTO_B2_KEY_ID",
        "HFT3_CRYPTO_B2_APP_KEY",
        "HFT3_CRYPTO_B2_BUCKET",
        "HFT3_CRYPTO_B2_SOURCE_BUCKET",
        "HFT3_CRYPTO_B2_ENDPOINT",
        "BTC_RPC_URL",
        "BTC_RPC_USER",
        "BTC_RPC_PASS",
        "CRYPTOCOMPARE_API_KEY",
        "ETHERSCAN_API_KEY",
        "FRED_API_KEY",
        "HFT3_CRYPTO_KRAKEN_API_KEY",
        "HFT3_CRYPTO_KRAKEN_PRIVATE_KEY",
    ]
    return {
        "loaded_files": [str(p) for p in _LOADED_FILES],
        "keys": env_status(*keys),
    }
