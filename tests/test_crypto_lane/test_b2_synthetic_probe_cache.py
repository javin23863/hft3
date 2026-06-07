"""B2 synthetic probe disk cache."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_lane.src.ingest.b2_synthetic_probe_cache import (
    load_cached_b2_synthetic_probe,
    save_cached_b2_synthetic_probe,
)


def test_b2_synthetic_probe_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ingest.b2_synthetic_probe_cache.repo_root_from_lane",
        lambda: tmp_path,
    )
    days = ["2024-04-02", "2024-04-03"]
    probe = {
        "bucket": "crypto-alpha-datasets",
        "available_days": ["2024-04-02"],
        "missing_days": ["2024-04-03"],
        "available_count": 1,
        "missing_count": 1,
        "error_samples": [],
        "sampled": False,
        "probe_days": 2,
    }
    save_cached_b2_synthetic_probe(days, probe)
    loaded = load_cached_b2_synthetic_probe(days)
    assert loaded is not None
    assert loaded["available_count"] == 1
    assert loaded.get("from_cache") is True


def test_b2_synthetic_probe_cache_rejects_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crypto_lane.src.ingest.b2_synthetic_probe_cache.repo_root_from_lane",
        lambda: tmp_path,
    )
    days = ["2024-04-02"]
    save_cached_b2_synthetic_probe(days, {"available_count": 0, "missing_count": 1})
    path = tmp_path / "runtime/data_audits/b2_synthetic_probe_cache.json"
    import json

    doc = json.loads(path.read_text(encoding="utf-8"))
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    doc["probed_at"] = old
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert load_cached_b2_synthetic_probe(days) is None
