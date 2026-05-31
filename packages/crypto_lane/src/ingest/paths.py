"""Resolve data/crypto paths from universe config."""
from __future__ import annotations

from pathlib import Path

from crypto_lane.src.config_loader import load_universe
from crypto_lane.src.types import repo_root_from_lane


def data_root() -> Path:
    uni = load_universe()
    rel = uni.get("paths", {}).get("data_root", "data/crypto")
    return repo_root_from_lane() / rel


def bronze_dir() -> Path:
    return data_root() / "bronze"


def normalized_dir() -> Path:
    return data_root() / "normalized"


def ensure_data_dirs() -> None:
    bronze_dir().mkdir(parents=True, exist_ok=True)
    normalized_dir().mkdir(parents=True, exist_ok=True)
