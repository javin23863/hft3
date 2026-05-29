"""Tests for options quote ingest and alignment."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from options_lane.src.config_loader import load_group_by_id, load_universe
from options_lane.src.ingest.quote_aligner import align_quotes, snapshot_has_required_legs
from options_lane.src.ingest.quotes_io import load_quote_ndjson
from options_lane.src.models import LegQuote

_CONFIG = _REPO / "options_lane" / "config" / "parity_universe.yaml"
_FAIR_FIXTURE = _REPO / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson"


def test_load_fixture_ndjson() -> None:
    quotes = load_quote_ndjson(_FAIR_FIXTURE)
    assert len(quotes) == 6
    roles = {q.role for q in quotes}
    assert roles == {"future", "call", "put"}


def test_align_quotes_produces_snapshots() -> None:
    group = load_group_by_id(_CONFIG, "example_same_ul")
    quotes = load_quote_ndjson(_FAIR_FIXTURE)
    snapshots = align_quotes(group, quotes)
    assert len(snapshots) >= 2
    assert snapshots[-1].timestamp_ns == 2_000_000_000
    assert snapshot_has_required_legs(group, snapshots[-1])


def test_missing_leg_not_complete() -> None:
    group = load_group_by_id(_CONFIG, "example_same_ul")
    quotes = [
        LegQuote("future", "ES.c.0", 5500.0, 5500.0, 1_000_000_000),
        LegQuote("call", "ES.c.0", 80.0, 80.0, 1_000_000_000, strike=5500, right="C"),
    ]
    snapshots = align_quotes(group, quotes)
    assert snapshots
    assert not snapshot_has_required_legs(group, snapshots[-1])


def test_lane_quarantine_paths_not_under_production_npz() -> None:
    repo_root, _, paths = load_universe(_CONFIG)
    prod_npz = (repo_root / "data" / "npz").resolve()
    for label, p in paths.items():
        resolved = p.resolve()
        with pytest.raises(ValueError):
            resolved.relative_to(prod_npz)
        assert "options" in str(resolved) or "parity" in str(resolved), f"{label} should be quarantined lane path"


def test_write_under_options_raw(tmp_path: Path) -> None:
    _, _, paths = load_universe(_CONFIG)
    raw = tmp_path / "data" / "options" / "raw" / "test.ndjson"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"role":"future","symbol":"X","bid":1,"ask":1,"timestamp_ns":1}\n', encoding="utf-8")
    assert raw.exists()
    assert "options" in str(raw)
