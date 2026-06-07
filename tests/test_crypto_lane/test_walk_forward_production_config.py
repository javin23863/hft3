"""Production smoke config guards."""
from __future__ import annotations

import pytest

from crypto_lane.src.ml.walk_forward_runner import backtest_config_path, run_smoke


def test_run_smoke_production_requires_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.repo_root_from_lane",
        lambda: tmp_path,
    )
    missing = backtest_config_path("crypto_h1_basis_compression", production=True)
    assert not missing.is_file()
    with pytest.raises(FileNotFoundError, match="production backtest config missing"):
        run_smoke("crypto_h1_basis_compression", production=True)


def test_run_smoke_production_yaml_has_validation_mode_production():
    from crypto_lane.src.config_loader import load_yaml

    path = backtest_config_path("crypto_h1_basis_compression", production=True)
    doc = load_yaml(path)
    assert doc.get("validation_mode") == "production"
