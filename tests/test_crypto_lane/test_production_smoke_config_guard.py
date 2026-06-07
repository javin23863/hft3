"""Prove production smoke guards without crypto_ready gate."""
from __future__ import annotations

import pytest

from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ml.walk_forward_runner import backtest_config_path, run_smoke


def test_all_production_backtest_configs_use_validation_mode_production():
    from crypto_lane.src.config_loader import list_backtest_config_paths

    paths = list_backtest_config_paths(include_production=True)
    assert len(paths) == 7
    for path in paths:
        doc = load_yaml(path)
        assert doc.get("validation_mode") == "production", path.name


def test_production_smoke_path_uses_fixture_data(monkeypatch):
    from crypto_lane.src.features.feature_matrix import build_labeled_frame as real_build
    from crypto_lane.src.ml.walk_forward_runner import candidate_by_id

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    fixtures = root / "packages/crypto_lane/fixtures"
    bt = load_yaml(backtest_config_path("crypto_h1_basis_compression", production=True))
    assert bt.get("validation_mode") == "production"

    def _build_with_fixture_data(**kwargs):
        backtest = dict(kwargs.get("backtest_config") or {})
        assert backtest.get("validation_mode") == "production"
        backtest["validation_mode"] = "fixture"
        kwargs["backtest_config"] = backtest
        return real_build(**kwargs)

    def _ridge_only_candidate(candidate_id: str):
        cand = dict(candidate_by_id(candidate_id))
        cand["challengers"] = ["ridge"]
        cand["baseline"] = ["ridge"]
        return cand

    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.synthetic_bookticker_days",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.resolve_label_horizon_ms",
        lambda horizons, backtest=None: 1000,
    )
    monkeypatch.setattr(
        "crypto_lane.src.config.data_paths.resolve_lane_data_dir",
        lambda backtest=None: fixtures,
    )
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.build_labeled_frame",
        _build_with_fixture_data,
    )
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.candidate_by_id",
        _ridge_only_candidate,
    )

    report = run_smoke("crypto_h1_basis_compression", production=True)
    assert report["pass_fail"] == "pass"
    assert report.get("smoke_mode") is False


def test_production_smoke_challenger_hard_fail_without_optional_deps(monkeypatch):
    from crypto_lane.src.features.feature_matrix import build_labeled_frame as real_build

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    fixtures = root / "packages/crypto_lane/fixtures"

    def _build(**kwargs):
        bt = dict(kwargs.get("backtest_config") or {})
        bt["validation_mode"] = "fixture"
        kwargs["backtest_config"] = bt
        return real_build(**kwargs)

    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.synthetic_bookticker_days",
        lambda **_: [],
    )
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.resolve_label_horizon_ms",
        lambda horizons, backtest=None: 1000,
    )
    monkeypatch.setattr(
        "crypto_lane.src.config.data_paths.resolve_lane_data_dir",
        lambda backtest=None: fixtures,
    )
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.build_labeled_frame",
        _build,
    )
    report = run_smoke("crypto_h1_basis_compression", production=True)
    if report["pass_fail"] == "fail":
        assert "challenger unavailable" in (report.get("rejection_reason") or "")
    else:
        pytest.skip("lightgbm/xgboost installed; hard-fail path not exercised")


def test_production_smoke_blocked_when_synthetic_days_present(monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.synthetic_bookticker_days",
        lambda **_: ["2024-04-02"],
    )
    with pytest.raises(ValueError, match="synthetic bookticker"):
        run_smoke("crypto_h1_basis_compression", production=True)


def test_pit_strict_production_blocked_without_live_rtt(monkeypatch):
    bt = load_yaml(backtest_config_path("crypto_h4_mempool_volatility", production=True))
    assert bt.get("btc_node_feature_availability_mode") == "pit_strict"
    from crypto_lane.src.ml.walk_forward_runner import _assert_production_ready

    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.synthetic_bookticker_days",
        lambda **_: [],
    )
    with pytest.raises(ValueError, match="pit_strict blocked"):
        _assert_production_ready(bt)
