"""Screener filter tests."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"


def test_fixture_passes_screener():
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.screen.universe_screener import screen_session

    _, universe, paths = load_universe(CONFIG)
    result = screen_session(
        FIXTURE,
        universe.filters,
        paths["float_metadata"],
        paths["daily_bars"],
        daily_bars_fallback=paths["daily_bars_fixture"],
        l3_only=universe.l3_only,
        allow_degraded=True,
    )
    assert result.passed, result.to_dict()
    enabled = [f for f in result.filters if f.enabled and f.status != "skip"]
    assert all(f.status == "pass" for f in enabled)


def test_disabled_filter_skips():
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.screen.universe_screener import screen_session
    from equities_lane.src.types import FilterConfig

    _, universe, paths = load_universe(CONFIG)
    filters = FilterConfig(
        float_max_shares=(False, 20_000_000),
        min_premarket_gap_pct=universe.filters.min_premarket_gap_pct,
        min_rvol=universe.filters.min_rvol,
        rvol_lookback_days=universe.filters.rvol_lookback_days,
        min_premarket_volume=universe.filters.min_premarket_volume,
        min_float_rotation=universe.filters.min_float_rotation,
        prior_runner=universe.filters.prior_runner,
        min_prior_run_pct=universe.filters.min_prior_run_pct,
        overhead_resistance=False,
        consolidation_days=20,
    )
    result = screen_session(
        FIXTURE,
        filters,
        paths["float_metadata"],
        paths["daily_bars"],
        daily_bars_fallback=paths["daily_bars_fixture"],
        l3_only=universe.l3_only,
        allow_degraded=True,
    )
    float_row = next(f for f in result.filters if f.name == "float_max_shares")
    assert float_row.status == "skip"
