"""Feature pipeline degraded mode tests."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"


def test_features_on_fixture_degraded():
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.features.feature_registry import run_feature_pipeline
    from equities_lane.src.ingest.session_io import load_session

    _, universe, _ = load_universe(CONFIG)
    meta, ticks = load_session(FIXTURE)
    snaps = run_feature_pipeline(ticks, universe, meta.degraded)
    assert len(snaps) == len(ticks)
    assert meta.degraded.degraded_mode
    assert snaps[-1].l3.get("degraded") is True
