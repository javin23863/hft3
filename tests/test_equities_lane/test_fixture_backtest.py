"""End-to-end fixture backtest."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"


def test_fixture_backtest_runs():
    from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(CONFIG)
    bt = LowFloatBacktester(universe)
    result = bt.run(str(FIXTURE), allow_degraded=True)
    assert result.symbol == "RUNNER"
    assert result.degraded.degraded_mode


def test_pipeline_fixture_backtest_cli():
    from equities_lane.pipeline import main

    assert main(["fixture-backtest"]) == 0
