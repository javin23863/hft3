"""Tests for OPRA options chain loader and Black-Scholes greeks."""
from __future__ import annotations

import math
import json
from datetime import date
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
    assert d["iv_atm_status"] == "SUCCESS"
    assert d["iv_confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert d["real_option_chain_available"] is True


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


def test_iv_failure_reports_no_arbitrage_status(tmp_path):
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    p = tmp_path / "bad_chain.ndjson"
    row = {
        "quote_ts_ns": 1_596_000_000_000_000_000,
        "symbol": "BAD",
        "strike": 3.5,
        "right": "C",
        "expiry": "2020-08-21",
        "bid": 10.0,
        "ask": 10.2,
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")

    snap = OptionsChainLoader(p, underlying="BAD").to_snapshot(
        row["quote_ts_ns"],
        spot=3.65,
        decision_date=date(2020, 7, 28),
    )

    assert snap.iv_atm == 0.0
    assert snap.iv_atm_status == "NO_ARBITRAGE_FAIL"
    assert snap.iv_confidence == "BLOCKED"
    assert snap.no_arbitrage_violation_count == 1


def test_synthetic_only_chain_is_not_real_market_evidence(tmp_path):
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    p = tmp_path / "synthetic_chain.ndjson"
    row = {
        "quote_ts_ns": 1_596_000_000_000_000_000,
        "symbol": "SYN",
        "strike": 10.0,
        "right": "C",
        "expiry": "2020-08-21",
        "bid": 0.4,
        "ask": 0.5,
        "source": "PROXY_SYNTHETIC",
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")

    snap = OptionsChainLoader(p, underlying="SYN").to_snapshot(
        row["quote_ts_ns"],
        spot=10.0,
        decision_date=date(2020, 7, 28),
    )

    assert snap.real_quote_count == 0
    assert snap.synthetic_quote_count == 1
    assert snap.surface_source == "PROXY_SYNTHETIC"
    assert snap.iv_atm_status == "SYNTHETIC_LOW_CONFIDENCE"
    assert snap.iv_confidence == "BLOCKED"


def test_mixed_synthetic_rows_do_not_contaminate_executable_snapshot(tmp_path):
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    ts = 1_596_000_000_000_000_000
    rows = [
        {
            "quote_ts_ns": ts,
            "symbol": "MIX",
            "strike": 10.0,
            "right": "C",
            "expiry": "2020-08-21",
            "bid": 0.4,
            "ask": 0.5,
            "bid_size": 10,
            "ask_size": 10,
            "listed_at_ts_ns": ts - 1,
        },
        {
            "quote_ts_ns": ts,
            "symbol": "MIX",
            "strike": 10.0,
            "right": "P",
            "expiry": "2020-08-21",
            "bid": 0.4,
            "ask": 0.5,
            "source": "PROXY_SYNTHETIC",
        },
    ]
    p = tmp_path / "mixed_chain.ndjson"
    p.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    snap = OptionsChainLoader(p, underlying="MIX").to_snapshot(
        ts,
        spot=10.0,
        decision_date=date(2020, 7, 28),
    )

    assert snap.num_quotes == 2
    assert snap.real_quote_count == 1
    assert snap.synthetic_quote_count == 1
    assert len(snap.quotes) == 1
    assert snap.quotes[0].right == "C"
    assert snap.pc_ratio_volume == 0.0
    assert snap.real_nbbo_size_available is True
    assert snap.valid_contract_count == 1


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
