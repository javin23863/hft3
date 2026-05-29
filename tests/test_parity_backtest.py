"""Tests for multi-leg parity backtester."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from options_lane.src.backtest.multi_leg_backtester import MultiLegParityBacktester, write_research_card
from options_lane.src.backtest.options_fee_model import OptionsFeeModel
from options_lane.src.config_loader import load_group_by_id, load_universe
from options_lane.src.ingest.quotes_io import load_quote_ndjson
from options_lane.src.models import LegQuote

_CONFIG = _REPO / "options_lane" / "config" / "parity_universe.yaml"
_FAIR = _REPO / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson"
_VIOLATION = _REPO / "options_lane" / "fixtures" / "violation_futures_quotes.ndjson"


def test_zero_violation_zero_arbs() -> None:
    group = load_group_by_id(_CONFIG, "example_same_ul")
    quotes = load_quote_ndjson(_FAIR)
    # Use only first timestamp snapshot (fair)
    fair_only = [q for q in quotes if q.timestamp_ns == 1_000_000_000]
    bt = MultiLegParityBacktester(fee_model=OptionsFeeModel(fee_per_contract=0.0), latency_ms=0.0)
    result = bt.run(group, fair_only)
    assert result.num_arbs == 0
    assert result.net_pnl == 0.0


def test_violation_positive_pnl_after_costs(tmp_path: Path) -> None:
    group = load_group_by_id(_CONFIG, "example_same_ul")
    quotes = load_quote_ndjson(_VIOLATION)
    bt = MultiLegParityBacktester(fee_model=OptionsFeeModel(fee_per_contract=0.10), latency_ms=0.0)
    result = bt.run(group, quotes)
    assert result.num_arbs >= 1
    assert result.net_pnl > 0.0
    assert result.max_violation_ticks >= group.threshold_ticks
    _, _, paths = load_universe(_CONFIG)
    card = write_research_card(result, tmp_path)
    assert card.exists()
    assert card.read_text(encoding="utf-8").count("net_pnl") >= 1


def test_latency_deferral_no_instant_fill() -> None:
    group = load_group_by_id(_CONFIG, "example_same_ul")
    # Violation at t=1s; with 5ms latency fill should be at t>=1s+5ms
    quotes = load_quote_ndjson(_VIOLATION)
    quotes.append(
        LegQuote("future", "ES.c.0", 5500.0, 5500.0, 1_000_500_000)
    )
    bt = MultiLegParityBacktester(fee_model=OptionsFeeModel(fee_per_contract=0.0), latency_ms=5.0)
    result = bt.run(group, quotes)
    if result.fills:
        assert all(f.timestamp_ns >= 1_005_000_000 for f in result.fills)


def test_fixture_backtest_cli_smoke() -> None:
    import subprocess

    r = subprocess.run(
        [sys.executable, "-m", "options_lane.pipeline", "fixture-backtest"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
