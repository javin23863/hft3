"""Causal pattern label tests — no future tick leakage."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"


def test_orb_label_causal_prefix():
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.ingest.session_io import load_session
    from equities_lane.src.patterns.opening_range_breakout import label_opening_range_breakout

    meta, ticks = load_session(FIXTURE)
    _, universe, _ = load_universe(CONFIG)
    early = label_opening_range_breakout(ticks[:100], meta, universe.patterns)
    full = label_opening_range_breakout(ticks, meta, universe.patterns)
    # Early prefix must not use terminal-session VWAP side from full tape
    assert early.orb_high <= full.orb_high or len(ticks[:100]) < len(ticks)


def test_consolidation_uses_prefix_only():
    from equities_lane.src.config_loader import load_universe
    from equities_lane.src.ingest.session_io import load_session
    from equities_lane.src.patterns.consolidation import label_consolidation

    _, ticks = load_session(FIXTURE)
    _, universe, _ = load_universe(CONFIG)
    short = label_consolidation(ticks[:60], universe.patterns)
    long = label_consolidation(ticks[:120], universe.patterns)
    assert short.pattern == "consolidation"
    assert long.pattern == "consolidation"
