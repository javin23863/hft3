"""Smoke report includes challenger OOS IC keys."""
from __future__ import annotations

from crypto_lane.src.ml.walk_forward_runner import run_smoke

_EXPECTED_CHALLENGERS = ("lightgbm", "xgboost", "elastic_net", "ridge")


def test_smoke_oos_ic_challengers_keys():
    report = run_smoke("crypto_h1_basis_compression")
    primary = report["runs"].get("with_btc_node") or report["runs"].get("without_btc_node")
    assert primary is not None
    assert "oos_ic_challengers" in primary
    oos = primary["oos_ic_challengers"]
    for name in _EXPECTED_CHALLENGERS:
        assert name in oos
