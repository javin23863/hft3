"""Walk-forward no-lookahead tests."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"


def test_float_lookup_no_lookahead():
    from equities_lane.src.backtest.walk_forward import assert_no_float_lookahead
    from equities_lane.src.config_loader import load_universe

    _, _, paths = load_universe(CONFIG)
    check = assert_no_float_lookahead(
        str(paths["float_metadata"]),
        "GME",
        "2021-01-27",
    )
    assert check["lookahead_ok"]
    assert check["float_as_of"] == "2021-01-01"


def test_generate_folds():
    from equities_lane.src.backtest.walk_forward import generate_folds
    from equities_lane.src.config_loader import load_universe

    _, universe, _ = load_universe(CONFIG)
    folds = generate_folds("2024-01-01", "2024-06-01", universe)
    assert len(folds) >= 1
    assert folds[0].train_start == "2024-01-01"
