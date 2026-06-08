"""Integration test: LowFloatBacktester consumes options loader."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"
OPRA = REPO / "packages" / "equities_lane" / "fixtures" / "opra_chain_v1.ndjson"


def test_backtester_with_options_loader_produces_features():
    from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.options.chain_loader import OptionsChainLoader

    _, universe, _ = load_universe(CONFIG)
    loader = OptionsChainLoader(OPRA, underlying="RUNNER")
    bt = LowFloatBacktester(universe, options_loader=loader)
    res = bt.run(str(FIXTURE), allow_degraded=True)
    assert res.symbol == "RUNNER"
    d = res.to_dict()
    assert "fills" in d


def test_backtester_without_options_loader_still_runs():
    from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(CONFIG)
    bt = LowFloatBacktester(universe)
    res = bt.run(str(FIXTURE), allow_degraded=True)
    assert res.symbol == "RUNNER"


def test_features_with_options_attach_to_snapshot():
    from equities_lane.src.features.book_adapter import compute_features
    from equities_lane.src.features.l3_stubs import compute_l3_features
    from equities_lane.src.ingest.session_io import load_session
    from equities_lane.src.models import SessionTick
    from equities_lane.src.options.chain_loader import OptionsChainLoader
    from equities_lane.src.types import DegradedModeFlags, FeatureToggles

    meta, ticks = load_session(FIXTURE)
    toggles = FeatureToggles(ofi=False, vpin=False, hawkes=False, hmm=False,
                              l3_queue=False, l3_cancellation=False, l3_iceberg=False)
    loader = OptionsChainLoader(OPRA, underlying="RUNNER")
    snaps = compute_features(ticks, toggles, DegradedModeFlags(degraded_mode=True, assumptions=["fixture"]), options_loader=loader)
    assert len(snaps) == len(ticks)
    opt_keys = snaps[0].to_dict().get("options", {})
    assert "iv_atm" in opt_keys
    assert "gex_net" in opt_keys
