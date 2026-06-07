from __future__ import annotations

from pathlib import Path

from crypto_lane.src.ingest import mempool_preflight, node_remote_sync


def test_sync_chi404_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("HFT3_CHI404_NODE_ENABLED", "0")
    report = node_remote_sync.sync_chi404_btc_node_artifacts()
    assert report.get("skipped")


def test_sync_chi404_unreachable(monkeypatch):
    monkeypatch.setenv("HFT3_CHI404_NODE_ENABLED", "1")
    monkeypatch.setattr(node_remote_sync, "ssh_probe", lambda *a, **k: False)
    report = node_remote_sync.sync_chi404_btc_node_artifacts()
    assert report["reachable"] is False


def test_local_mempool_jsonl_days(tmp_path: Path, monkeypatch):
    from crypto_lane.src.ingest import paths

    gold = tmp_path / "gold" / "bitcoind" / "mempool"
    gold.mkdir(parents=True)
    (gold / "2024-01-01_mempool_snapshot.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(node_remote_sync, "gold_dir", lambda: tmp_path / "gold")
    days = node_remote_sync.local_mempool_jsonl_days(start="2024-01-01", end="2024-01-31")
    assert days == ["2024-01-01"]


def test_preflight_counts_local_jsonl_without_b2(monkeypatch):
    monkeypatch.setattr(
        mempool_preflight,
        "_mempool_probe",
        lambda days, **_: {
            "bucket": "crypto-alpha-datasets",
            "local_jsonl_days": [],
            "local_jsonl_day_count": 0,
            "available_days": [],
            "missing_days": [d.isoformat() for d in days],
            "available_count": 0,
            "missing_count": len(days),
            "error_samples": [],
        },
    )
    monkeypatch.setattr(mempool_preflight, "_read_btc_node_status", lambda: None)
    monkeypatch.setattr(mempool_preflight, "_normalized_mempool_covers_range", lambda *a, **k: False)
    monkeypatch.setattr(
        mempool_preflight,
        "local_mempool_jsonl_days",
        lambda **_: ["2024-01-01", "2024-01-02"],
    )
    report = mempool_preflight.preflight_mempool_gaps(start="2024-01-01", end="2024-01-02")
    assert report["local_mempool_jsonl_day_count"] == 2
    assert report["mempool_ready"] is True
