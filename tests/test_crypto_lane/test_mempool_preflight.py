from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from crypto_lane.src.ingest import mempool_preflight


def test_preflight_mempool_ready_when_b2_full(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [d.isoformat() for d in days],
            "missing_days": [],
            "available_count": len(days),
            "missing_count": 0,
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: {"synced": True})
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: False)
    report = mempool_preflight.preflight_mempool_gaps(start="2024-01-01", end="2024-01-02")
    assert report["mempool_ready"] is True
    assert report["crypto_mempool_missing_days"] == 0
    assert report["btc_node_synced"] is True


def test_preflight_mempool_not_ready_without_b2_or_normalized(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": [date(2024, 1, 1).isoformat()],
            "available_count": 0,
            "missing_count": 1,
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: False)
    monkeypatch.setattr(mempool_preflight, "local_mempool_jsonl_days", lambda **_: [])
    report = mempool_preflight.preflight_mempool_gaps(start="2024-01-01", end="2024-01-01")
    assert report["mempool_ready"] is False
    assert report["btc_node_synced"] is None


def test_preflight_mempool_ready_at_coverage_threshold(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [days[0].isoformat()],
            "missing_days": [days[1].isoformat()],
            "available_count": 19,
            "missing_count": 1,
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: False)
    report = mempool_preflight.preflight_mempool_gaps(start="2024-01-01", end="2024-01-20")
    assert report["mempool_ready"] is True


def test_preflight_mempool_normalized_only_not_ready(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [],
            "missing_days": [d.isoformat() for d in days],
            "available_count": 0,
            "missing_count": len(days),
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: True)
    monkeypatch.setattr(mempool_preflight, "local_mempool_jsonl_days", lambda **_: [])
    report = mempool_preflight.preflight_mempool_gaps(start="2024-01-01", end="2024-01-01")
    assert report["mempool_ready"] is False
    assert report["normalized_mempool_covers_range"] is True


def test_preflight_mempool_allow_degraded(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [days[0].isoformat()],
            "missing_days": [days[1].isoformat()],
            "available_count": 1,
            "missing_count": 1,
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: True)
    report = mempool_preflight.preflight_mempool_gaps(
        start="2024-01-01", end="2024-01-02", allow_degraded_mempool=True
    )
    assert report["mempool_ready"] is True


def test_preflight_sampled_probe_extrapolates(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "available_days": [d.isoformat() for d in days],
            "missing_days": [],
            "available_count": len(days),
            "missing_count": 0,
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: False)
    report = mempool_preflight.preflight_mempool_gaps(
        start="2024-01-01",
        end="2024-03-31",
        b2_probe_max_days=10,
    )
    assert report["b2_probe_sampled"] is True
    assert report["b2_probe_days"] == 10
    assert report["mempool_ready"] is True
