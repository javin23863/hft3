"""Resolve fixture vs production data paths for crypto lane."""
from __future__ import annotations

from pathlib import Path

from crypto_lane.src.config_loader import load_universe
from crypto_lane.src.types import repo_root_from_lane

_REQUIRED_PRODUCTION = (
    "spot_perp_ticks.csv",
    "deribit_surface.csv",
    "mempool_snapshots.csv",
)


def resolve_lane_data_dir(backtest: dict | None = None) -> Path:
    mode = (backtest or {}).get("validation_mode", "fixture")
    uni = load_universe()
    paths = uni.get("paths", {})
    root = repo_root_from_lane()
    if mode == "fixture":
        return root / paths.get("fixtures_root", "packages/crypto_lane/fixtures")
    norm = root / paths.get("data_root", "data/crypto") / "normalized"
    missing = [name for name in _REQUIRED_PRODUCTION if not (norm / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Production crypto data missing in {norm}: {missing}. "
            "Run: python -m crypto_lane.pipeline ingest --start YYYY-MM-DD --end YYYY-MM-DD"
        )
    return norm


def data_provenance_source(backtest: dict | None = None) -> str:
    mode = (backtest or {}).get("validation_mode", "fixture")
    return "crypto_lane_fixture" if mode == "fixture" else "crypto_lane_production"
