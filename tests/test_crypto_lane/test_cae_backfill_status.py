"""CAE/B2 bookticker backfill status probe."""
from __future__ import annotations

from unittest.mock import patch

from crypto_lane.src.ingest.cae_backfill_status import cae_bookticker_backfill_status


def test_cae_status_no_synthetic():
    pf = {
        "synthetic_day_list": [],
        "synthetic_days": 0,
        "purge_safe": True,
        "purge_block_reason": None,
        "b2": {"available_count": 0},
        "b2_synthetic": {"available_count": 0, "sampled": False},
    }
    report = cae_bookticker_backfill_status(
        start="2024-01-01", end="2024-03-31", l3_preflight=pf
    )
    assert report["synthetic_days"] == 0
    assert report["recommendation"] == "no_synthetic_days"
    assert report["days_until_purge_safe"] == 0


def test_cae_status_needs_backfill():
    pf = {
        "synthetic_day_list": ["2024-04-02", "2024-04-03"],
        "synthetic_days": 2,
        "purge_safe": False,
        "purge_block_reason": "B2 has 0/2 synthetic-replacement days",
        "b2": {"available_count": 0},
        "b2_synthetic": {"available_count": 0, "sampled": False},
    }
    report = cae_bookticker_backfill_status(
        start="2024-01-01", end="2024-12-31", l3_preflight=pf
    )
    assert report["synthetic_days"] == 2
    assert report["recommendation"] == "cae_contabo_bookticker_backfill_required"
    assert report["days_until_purge_safe"] == 2
    assert report["synthetic_by_month"]["2024-04"] == 2


def test_cae_status_purge_ready():
    pf = {
        "synthetic_day_list": ["2024-04-02"],
        "synthetic_days": 1,
        "purge_safe": True,
        "purge_block_reason": None,
        "b2": {"available_count": 0},
        "b2_synthetic": {"available_count": 1, "sampled": False},
    }
    report = cae_bookticker_backfill_status(
        start="2024-01-01", end="2024-12-31", l3_preflight=pf
    )
    assert report["purge_safe"] is True
    assert report["recommendation"] == "ready_for_replace_synthetic"
    assert report["days_until_purge_safe"] == 0


def test_cae_status_delegates_to_preflight_when_not_passed():
    with patch(
        "crypto_lane.src.ingest.cae_backfill_status.preflight_l3_gaps",
        return_value={
            "synthetic_day_list": [],
            "synthetic_days": 0,
            "purge_safe": True,
            "b2": {"available_count": 0},
            "b2_synthetic": {"available_count": 0},
        },
    ) as mock_pf:
        cae_bookticker_backfill_status(start="2024-01-01", end="2024-01-31")
    mock_pf.assert_called_once()
