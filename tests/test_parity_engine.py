"""Tests for put/call parity engine (config-driven, no hardcoded symbols)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from options_lane.src.config_loader import load_group_by_id, load_universe
from options_lane.src.ingest.quote_aligner import align_quotes
from options_lane.src.models import LegQuote, ParityGroup, QuoteSnapshot, RateSpec
from options_lane.src.parity_engine import compute_violation, discount_factor, is_actionable, theoretical_spread

_CONFIG = _REPO / "options_lane" / "config" / "parity_universe.yaml"


def _futures_group(**overrides) -> ParityGroup:
    base = {
        "id": "test_fut",
        "type": "futures_options",
        "rate": {"source": "constant", "value": 0.05},
        "legs": [
            {"role": "future", "symbol": "UL.c.0"},
            {"role": "call", "symbol": "UL.c.0", "strike": 5500, "right": "C"},
            {"role": "put", "symbol": "UL.c.0", "strike": 5500, "right": "P"},
        ],
        "threshold_ticks": 1.0,
        "tick_size": 0.25,
        "time_to_expiry_years": 0.25,
    }
    base.update(overrides)
    return ParityGroup.from_dict(base)


def _fair_quotes(future_mid: float = 5500.0, strike: float = 5500.0, rate: float = 0.05, t_years: float = 0.25) -> dict[str, LegQuote]:
    df = discount_factor(rate, t_years)
    theo = future_mid - df * strike
    call_mid = theo / 2 + 10
    put_mid = call_mid - theo
    ts = 1_000_000_000
    return {
        "future": LegQuote("future", "UL.c.0", future_mid, future_mid, ts),
        "call": LegQuote("call", "UL.c.0", call_mid, call_mid, ts, strike=strike, right="C"),
        "put": LegQuote("put", "UL.c.0", put_mid, put_mid, ts, strike=strike, right="P"),
    }


def test_zero_residual_on_fair_prices() -> None:
    group = _futures_group()
    quotes = _fair_quotes()
    snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
    v = compute_violation(group, snap)
    assert v is not None
    assert abs(v.residual) < 1e-9
    assert v.edge_ticks < group.threshold_ticks
    assert not is_actionable(v, group)


def test_injected_mispricing_flags_violation() -> None:
    group = _futures_group()
    quotes = _fair_quotes()
    # Inject 2 ticks (0.50) mispricing on call
    quotes["call"] = LegQuote(
        "call", "UL.c.0", quotes["call"].mid + 0.50, quotes["call"].mid + 0.50,
        1_000_000_000, strike=5500, right="C",
    )
    snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
    v = compute_violation(group, snap, fee_per_leg=0.0)
    assert v is not None
    assert abs(v.residual - 0.50) < 1e-9
    assert v.edge_ticks >= 2.0
    assert is_actionable(v, group)


def test_filtration_quote_at_t_plus_one_not_visible() -> None:
    group = _futures_group()
    fair = list(_fair_quotes().values())
    late_future = LegQuote("future", "UL.c.0", 99999.0, 99999.0, 2_000_000_000)
    snapshots = align_quotes(group, fair + [late_future])
    v_at_1s = compute_violation(group, snapshots[0])
    assert v_at_1s is not None
    assert abs(v_at_1s.residual) < 1e-9
    snap_at_2s = snapshots[-1]
    assert snap_at_2s.quotes["call"].timestamp_ns == 1_000_000_000
    assert snap_at_2s.quotes["future"].timestamp_ns == 2_000_000_000
    v_filtrated = compute_violation(group, snap_at_2s, as_of_ns=1_000_000_000)
    assert v_filtrated is None
    v_at_2s = compute_violation(group, snap_at_2s)
    assert v_at_2s is not None
    assert v_at_2s.edge_ticks >= 2.0


def test_config_swap_different_group_no_code_change() -> None:
    g1 = load_group_by_id(_CONFIG, "example_same_ul")
    g2 = load_group_by_id(_CONFIG, "example_cross_ul")
    assert g1.type == "futures_options"
    assert g2.type == "index_future_basis"
    assert g1.legs[0].symbol != g2.legs[0].symbol or g1.legs[0].role != g2.legs[0].role


def test_cross_underlying_basis_model() -> None:
    group = ParityGroup.from_dict(
        {
            "id": "cross_test",
            "type": "index_future_basis",
            "basis_model": "index_minus_future",
            "basis_params": {"basis_scale": 1.0},
            "rate": {"source": "constant", "value": 0.05},
            "legs": [
                {"role": "spot", "symbol": "IDX"},
                {"role": "future", "symbol": "FUT.c.0"},
                {"role": "call", "symbol": "IDX", "strike": 5500, "right": "C"},
                {"role": "put", "symbol": "IDX", "strike": 5500, "right": "P"},
            ],
            "threshold_ticks": 1.0,
            "tick_size": 0.05,
            "time_to_expiry_years": 0.25,
        }
    )
    spot_mid = 5500.0
    df = discount_factor(0.05, 0.25)
    s_eff = spot_mid + 1.0 * (5501.0 - spot_mid)
    theo = s_eff - df * 5500.0
    call_mid = theo + 5.0
    put_mid = 5.0
    quotes = {
        "spot": LegQuote("spot", "IDX", spot_mid, spot_mid, 1_000_000_000),
        "future": LegQuote("future", "FUT.c.0", 5501.0, 5501.0, 1_000_000_000),
        "call": LegQuote("call", "IDX", call_mid, call_mid, 1_000_000_000, strike=5500, right="C"),
        "put": LegQuote("put", "IDX", put_mid, put_mid, 1_000_000_000, strike=5500, right="P"),
    }
    snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
    v = compute_violation(group, snap)
    assert v is not None
    assert abs(v.residual) < 1e-6


def test_theoretical_spread_matches_formula() -> None:
    group = _futures_group()
    quotes = _fair_quotes()
    theo = theoretical_spread(group, quotes)
    df = math.exp(-0.05 * 0.25)
    expected = 5500.0 - df * 5500.0
    assert abs(theo - expected) < 1e-9


def test_load_universe_quarantine_paths() -> None:
    repo_root, groups, paths = load_universe(_CONFIG)
    assert len(groups) >= 2
    assert "raw_root" in paths
    prod_npz = (repo_root / "data" / "npz").resolve()
    assert prod_npz not in paths["raw_root"].resolve().parents
