from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from crypto_lane.src.ingest import l3_preflight


@pytest.fixture(autouse=True)
def _no_b2_synthetic_cache(monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ingest.l3_preflight.load_cached_b2_synthetic_probe",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "crypto_lane.src.ingest.l3_preflight.save_cached_b2_synthetic_probe",
        lambda *a, **k: None,
    )


def test_preflight_purge_unsafe_when_b2_synthetic_empty(monkeypatch):
    monkeypatch.setattr(
        l3_preflight,
        "summarize_bookticker_range",
        lambda **_: {
            "missing": [date(2024, 4, 2)],
            "synthetic": ["2024-04-02"],
            "absent": [date(2024, 4, 2)],
            "by_class": {"synthetic": 1},
            "manifest": {},
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "b2_probe_bookticker_days",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": ["2024-04-02"],
            "available_count": 0,
            "missing_count": 1,
            "error_samples": [],
            "sampled": False,
            "probe_days": len(days),
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "b2_probe_bookticker_days_sampled",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": ["2024-04-02"],
            "available_count": 0,
            "missing_count": 1,
            "error_samples": [],
            "sampled": False,
            "probe_days": len(days),
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
    assert "synthetic-replacement" in report["purge_block_reason"]
    assert report["recommendation"] == "cae_b2_backfill_required_or_allow_degraded"


def test_preflight_purge_safe_when_b2_covers_synthetic(monkeypatch):
    monkeypatch.setattr(
        l3_preflight,
        "summarize_bookticker_range",
        lambda **_: {
            "missing": [date(2024, 4, 2)],
            "synthetic": ["2024-04-02"],
            "absent": [],
            "by_class": {"synthetic": 1},
            "manifest": {},
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "b2_probe_bookticker_days",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": ["2024-04-02"],
            "missing_days": [],
            "available_count": len(days),
            "missing_count": 0,
            "error_samples": [],
            "sampled": False,
            "probe_days": len(days),
        },
    )
    def _sampled_probe(days, **kwargs):
        if not days:
            return {
                "bucket": "crypto-alpha-datasets",
                "available_days": [],
                "missing_days": [],
                "available_count": 0,
                "missing_count": 0,
                "error_samples": [],
                "sampled": False,
                "probe_days": 0,
            }
        return {
            "bucket": "crypto-alpha-datasets",
            "available_days": ["2024-04-02"],
            "missing_days": [],
            "available_count": 1,
            "missing_count": 0,
            "error_samples": [],
            "sampled": False,
            "probe_days": 1,
        }

    monkeypatch.setattr(l3_preflight, "b2_probe_bookticker_days_sampled", _sampled_probe)
    report = l3_preflight.preflight_l3_gaps(
        start="2024-04-02", end="2024-04-02", vision_probe=False
    )
    assert report["purge_safe"] is True
    assert report["purge_safe_estimate"] is True
    assert report["recommendation"] == "run_fill_l3_gaps_replace_synthetic"


def test_preflight_purge_estimate_can_differ_from_full_probe(monkeypatch):
    monkeypatch.setattr(
        l3_preflight,
        "summarize_bookticker_range",
        lambda **_: {
            "missing": [date(2024, 4, 2), date(2024, 4, 3)],
            "synthetic": ["2024-04-02", "2024-04-03"],
            "absent": [],
            "by_class": {"synthetic": 2},
            "manifest": {},
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "b2_probe_bookticker_days",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": [d.isoformat() for d in days],
            "available_count": 0,
            "missing_count": len(days),
            "error_samples": [],
            "sampled": False,
            "probe_days": len(days),
        },
    )
    monkeypatch.setattr(
        l3_preflight,
        "b2_probe_bookticker_days_sampled",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": ["2024-04-02"],
            "missing_days": [],
            "available_count": 2,
            "missing_count": 0,
            "error_samples": [],
            "sampled": True,
            "probe_days": 1,
        },
    )
    report = l3_preflight.preflight_l3_gaps(
        start="2024-04-02",
        end="2024-04-03",
        vision_probe=False,
        full_synthetic_b2_probe=False,
        use_b2_synthetic_cache=False,
    )
    assert report["purge_safe"] is False
    assert report["purge_safe_estimate"] is True
    assert report["b2_synthetic"].get("skipped") is True


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
