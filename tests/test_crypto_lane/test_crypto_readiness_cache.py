"""Stale crypto_readiness.json cache rejection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_lane.src.ingest.crypto_readiness import readiness_cache_fresh


def test_fresh_cache_requires_zero_synthetic_and_match():
    now = datetime.now(UTC).isoformat()
    cached = {
        "crypto_ready": True,
        "audited_at": now,
        "synthetic_days": 0,
        "crypto_mempool_ready": True,
        "crypto_date_range": {"start": "2024-01-01", "end": "2024-12-31"},
    }
    assert readiness_cache_fresh(
        cached,
        live_synthetic_days=0,
        live_mempool_ready=True,
        live_norm_ok=True,
        expected_date_range={"start": "2024-01-01", "end": "2024-12-31"},
    )


def test_stale_age_rejected():
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    cached = {
        "crypto_ready": True,
        "audited_at": old,
        "synthetic_days": 0,
    }
    assert readiness_cache_fresh(cached, live_synthetic_days=0) is False


def test_synthetic_mismatch_rejected():
    now = datetime.now(UTC).isoformat()
    cached = {
        "crypto_ready": True,
        "audited_at": now,
        "synthetic_days": 0,
    }
    assert readiness_cache_fresh(cached, live_synthetic_days=274) is False


def test_mempool_decay_rejected():
    now = datetime.now(UTC).isoformat()
    cached = {
        "crypto_ready": True,
        "audited_at": now,
        "synthetic_days": 0,
        "crypto_mempool_ready": True,
    }
    assert readiness_cache_fresh(cached, live_synthetic_days=0, live_mempool_ready=False) is False


def test_date_range_mismatch_rejected():
    now = datetime.now(UTC).isoformat()
    cached = {
        "crypto_ready": True,
        "audited_at": now,
        "synthetic_days": 0,
        "crypto_date_range": {"start": "2024-01-01", "end": "2024-06-30"},
    }
    assert readiness_cache_fresh(
        cached,
        live_synthetic_days=0,
        expected_date_range={"start": "2024-01-01", "end": "2024-12-31"},
    ) is False
