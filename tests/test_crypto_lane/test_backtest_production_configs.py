"""Production backtest config load tests."""
from __future__ import annotations

from crypto_lane.src.config_loader import list_backtest_config_paths, load_yaml
from crypto_lane.src.ml.candidate_registry import validate_backtest_config
from crypto_lane.src.ml.walk_forward_runner import backtest_config_path


def test_seven_production_backtest_configs_load():
    paths = list_backtest_config_paths(include_production=True)
    prod = [p for p in paths if p.stem.endswith("_production")]
    assert len(prod) == 7
    for p in prod:
        doc = load_yaml(p)
        assert doc.get("validation_mode") == "production"
        errs = validate_backtest_config(doc)
        assert not errs, f"{p.name}: {errs}"


def test_backtest_config_path_production_suffix():
    p = backtest_config_path("crypto_h1_basis_compression", production=True)
    assert p.name == "h1_basis_compression_production.yaml"
