"""Prove production smoke code path without crypto_ready gate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from crypto_lane.src.ml.walk_forward_runner import run_smoke


def test_production_smoke_path_uses_fixture_data(monkeypatch):
    from crypto_lane.src.features.feature_matrix import build_labeled_frame as real_build
    from crypto_lane.src.ml.walk_forward_runner import candidate_by_id

    def _build_with_fixture_data(**kwargs):
        bt = dict(kwargs.get("backtest_config") or {})
        bt["validation_mode"] = "fixture"
        kwargs["backtest_config"] = bt
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


def test_production_smoke_blocked_when_synthetic_days_present(monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ml.walk_forward_runner.synthetic_bookticker_days",
        lambda **_: ["2024-04-02"],
    )
    with patch(
        "crypto_lane.src.config.data_paths.resolve_lane_data_dir",
    ):
        import pytest

        with pytest.raises(ValueError, match="synthetic bookticker"):
            run_smoke("crypto_h1_basis_compression", production=True)
