"""Backtest config load tests."""
from __future__ import annotations

from crypto_lane.src.config_loader import list_backtest_config_paths
from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ml.candidate_registry import validate_backtest_config


def test_seven_backtest_configs_load():
    paths = list_backtest_config_paths()
    assert len(paths) == 7
    for p in paths:
        doc = load_yaml(p)
        errs = validate_backtest_config(doc)
        assert not errs, f"{p.name}: {errs}"
