"""Tests for OPRA options chain loader and Black-Scholes greeks."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "opra_chain_v1.ndjson"


def test_loader_parses_fixture():
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    loader = OptionsChainLoader(FIXTURE, underlying="RUNNER")
    assert loader.num_bars == 3
    quotes = loader.lookup(loader._ts[0])
    assert quotes is not None
    assert all(q.mid > 0 for q in quotes)
    assert any(q.right == "C" for q in quotes)
    assert any(q.right == "P" for q in quotes)


def test_loader_to_snapshot_has_greeks():
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    loader = OptionsChainLoader(FIXTURE, underlying="RUNNER")
    snap = loader.to_snapshot(loader._ts[0], spot=10.0)
    assert snap.iv_atm > 0
    assert 0 < snap.coverage <= 1.0
    d = snap.to_dict()
    assert "iv_atm" in d
    assert "gex_net" in d
    assert "dex_net" in d
    assert "pc_ratio_volume" in d


def test_bs_implied_vol_recovers_input():
    from equities_lane.src.options.chain_loader import _bs_call, _bs_implied_vol, _bs_put

    spot = 100.0
    strike = 100.0
    iv = 0.30
    expiry = "2099-12-31"
    call_price = _bs_call(spot, strike, iv, expiry)
    put_price = _bs_put(spot, strike, iv, expiry)
    assert call_price > 0
    assert put_price > 0
    iv_call = _bs_implied_vol(call_price, spot, strike, expiry, "C")
    iv_put = _bs_implied_vol(put_price, spot, strike, expiry, "P")
    assert abs(iv_call - iv) < 0.01, f"call IV {iv_call} != {iv}"
    assert abs(iv_put - iv) < 0.01, f"put IV {iv_put} != {iv}"


def test_put_call_parity_holds():
    from equities_lane.src.options.chain_loader import _RISK_FREE, _bs_call, _bs_put, _years_to_expiry

    spot = 100.0
    strike = 100.0
    iv = 0.25
    expiry = "2099-12-31"
    T = _years_to_expiry(expiry)
    call = _bs_call(spot, strike, iv, expiry)
    put = _bs_put(spot, strike, iv, expiry)
    parity = call - put
    expected = spot - strike * math.exp(-_RISK_FREE * T)
    assert abs(parity - expected) < 0.5


def test_delta_call_in_unit_interval():
    from equities_lane.src.options.chain_loader import _bs_delta

    for strike in (80.0, 100.0, 120.0):
        d = _bs_delta(spot=100.0, strike=strike, iv=0.3, expiry="2099-12-31", right="C")
        assert 0.0 <= d <= 1.0
    for strike in (80.0, 100.0, 120.0):
        d = _bs_delta(spot=100.0, strike=strike, iv=0.3, expiry="2099-12-31", right="P")
        assert -1.0 <= d <= 0.0


def test_gamma_positive_for_both_sides():
    from equities_lane.src.options.chain_loader import _bs_gamma

    g = _bs_gamma(spot=100.0, strike=100.0, iv=0.3, expiry="2099-12-31")
    assert g > 0


def test_loader_lookup_uses_max_lag():
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    loader = OptionsChainLoader(FIXTURE, underlying="RUNNER")
    far_future = loader._ts[0] + 10**18
    assert loader.lookup(far_future, max_lag_ns=60 * 1_000_000_000) is None


def test_quarantine_path_isolation():
    from equities_lane.src.options.chain_loader import _options_ndjson_path

    p = _options_ndjson_path("any_session")
    assert "options" in str(p)
    assert "data/npz" not in str(p)
    prod_npz = (REPO / "data" / "npz").resolve()
    try:
        p.resolve().relative_to(prod_npz)
        raise AssertionError("options path leaked into data/npz")
    except ValueError as exc:
        if "data/npz" in str(exc):
            raise
