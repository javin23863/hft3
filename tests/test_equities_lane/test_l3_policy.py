"""L3-only lane policy tests."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"


def test_require_l3_rejects_degraded_fixture():
    from equities_lane.src.ingest.session_io import load_session
    from equities_lane.src.l3_policy import L3OnlyViolation, require_l3_session

    meta, _ = load_session(FIXTURE)
    with pytest.raises(L3OnlyViolation):
        require_l3_session(meta, l3_only=True, allow_degraded=False, context="backtest")


def test_require_l3_allows_degraded_when_flagged():
    from equities_lane.src.ingest.session_io import load_session
    from equities_lane.src.l3_policy import require_l3_session

    meta, _ = load_session(FIXTURE)
    require_l3_session(meta, l3_only=True, allow_degraded=True, context="backtest")
