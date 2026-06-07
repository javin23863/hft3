from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from crypto_lane.src.ingest import l3_preflight


def test_preflight_purge_unsafe_when_b2_empty(monkeypatch):
    monkeypatch.setattr(
        l3_preflight,
        "missing_bookticker_days",
        lambda **_: [date(2024, 4, 2)],
    )
    monkeypatch.setattr(
        l3_preflight,
        "synthetic_bookticker_days",
        lambda **_: ["2024-04-02"],
    )
    monkeypatch.setattr(
        l3_preflight,
        "_b2_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": ["2024-04-02"],
            "available_count": 0,
            "missing_count": 1,
            "error_samples": [{"day": "2024-04-02", "error": "File not present"}],
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "probe_vision_month",
        lambda *a, **k: "not_found",
    )
    report = l3_preflight.preflight_l3_gaps(start="2024-04-02", end="2024-04-02")
    assert report["purge_safe"] is False
    assert report["purge_block_reason"]
    assert report["recommendation"] == "cae_b2_backfill_required_or_allow_degraded"


def test_fill_aborts_unsafe_purge(monkeypatch):
    from crypto_lane.src.ingest.l3_gap_fill import fill_l3_gaps

    monkeypatch.setattr(
        "crypto_lane.src.ingest.l3_gap_fill.preflight_l3_gaps",
        lambda **_: {
            "purge_safe": False,
            "purge_block_reason": "blocked",
            "missing_days": 1,
        },
    )
    monkeypatch.setattr(
        "crypto_lane.src.ingest.l3_gap_fill.purge_synthetic_bookticker",
        lambda **_: pytest.fail("purge should not run"),
    )
    report = fill_l3_gaps(
        start="2024-04-02",
        end="2024-04-02",
        replace_synthetic=True,
        force=False,
    )
    assert report["aborted"] is True
    assert report["purged_synthetic_days"] == []
