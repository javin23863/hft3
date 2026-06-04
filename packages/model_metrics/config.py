"""Config loading for institutional model metric thresholds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_REL = Path("configs/model_metrics.yaml")


DEFAULT_CONFIG: dict[str, Any] = {
    "global": {
        "minimum_sharpe": 1.0,
        "minimum_sortino": 1.0,
        "minimum_calmar": 0.5,
        "maximum_drawdown": 1000.0,
        "maximum_drawdown_velocity": 250.0,
        "maximum_time_under_water": 20.0,
        "minimum_profit_factor": 1.1,
        "minimum_expectancy": 0.0,
        "maximum_slippage_bps": 10.0,
        "minimum_fill_rate": 0.8,
        "maximum_latency_ms": 100.0,
        "maximum_correlation_to_book": 0.75,
        "minimum_walk_forward_efficiency": 0.5,
        "maximum_fold_dispersion": 1.0,
        "maximum_pbo": 0.2,
        "minimum_deflated_sharpe": 0.0,
        "minimum_sample_size": 30,
        "yellow_multiplier": 0.8,
        "red_multiplier": 1.0,
        "kill_drawdown_multiplier": 1.5,
        "warning_drawdown_multiplier": 1.0,
        "data_freshness_max_ns": 5_000_000,
    },
    "asset_class_overrides": {},
    "symbol_overrides": {},
    "strategy_family_overrides": {},
    "regime_overrides": {},
    "venue_overrides": {},
    "mode_overrides": {
        "live": {},
        "paper": {},
        "shadow": {},
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_metrics_config(root: Path | str | None = None, path: Path | str | None = None) -> dict[str, Any]:
    repo = Path(root) if root is not None else Path.cwd()
    cfg_path = Path(path) if path is not None else repo / DEFAULT_CONFIG_REL
    if not cfg_path.is_file():
        return DEFAULT_CONFIG
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("model metrics config must be a mapping")
    return deep_merge(DEFAULT_CONFIG, raw)


def thresholds_for(
    config: dict[str, Any],
    *,
    asset_class: str = "",
    symbol: str = "",
    strategy_family: str = "",
    regime_id: str = "",
    venue: str = "",
    mode: str = "",
) -> dict[str, Any]:
    threshold = dict(config.get("global") or {})
    selectors = (
        ("asset_class_overrides", asset_class),
        ("symbol_overrides", symbol),
        ("strategy_family_overrides", strategy_family),
        ("regime_overrides", regime_id),
        ("venue_overrides", venue),
        ("mode_overrides", mode),
    )
    for section, key in selectors:
        if key:
            threshold = deep_merge(threshold, (config.get(section) or {}).get(str(key), {}))
    return threshold
