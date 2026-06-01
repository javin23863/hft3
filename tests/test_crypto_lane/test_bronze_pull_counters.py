"""Bronze pull counter hardening (Stream 2)."""
from __future__ import annotations

import polars as pl
import pytest

import crypto_lane.src.ingest.bronze_pull as bp
from crypto_lane.src.ingest.bronze_pull import pull_bronze
from crypto_lane.src.ingest.bronze_reader import BronzeReadError


@pytest.fixture
def bronze_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HFT3_CRYPTO_B2_KEY_ID", "fake")
    monkeypatch.setenv("HFT3_CRYPTO_B2_APP_KEY", "fake")
    monkeypatch.setattr(bp, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(bp, "ensure_crypto_env", lambda: None)
    monkeypatch.setattr(bp, "require_env", lambda *a, **k: None)
    monkeypatch.setattr(bp, "B2Client", lambda: None)
    monkeypatch.setattr(bp, "_local_cache_path", lambda key: tmp_path / key.replace("/", "__"))
    return tmp_path


def test_pull_bronze_mempool_does_not_increment_downloaded_for_cached_file(monkeypatch, bronze_env, tmp_path):
    cached = tmp_path / "already_here.parquet"
    cached.write_bytes(b"stub")

    def _fake_bronze_key(source, symbol, day, granularity):
        return "already_here.parquet"

    monkeypatch.setattr(bp, "bronze_key", _fake_bronze_key)

    called = {"n": 0}

    def _fake_read_bronze_day(*args, **kwargs):
        called["n"] += 1
        return pl.DataFrame()

    monkeypatch.setattr(bp, "read_bronze_day", _fake_read_bronze_day)

    counts = pull_bronze(start="2024-01-01", end="2024-01-01", sources=["mempool"])
    assert counts["downloaded"] == 0
    assert counts["skipped"] >= 1
    assert called["n"] == 0


def test_pull_bronze_continues_after_single_day_error(monkeypatch, bronze_env):
    days_called: list[str] = []

    def _fake_bronze_key(source, symbol, day, granularity):
        return f"parquet_{day.isoformat()}.parquet"

    monkeypatch.setattr(bp, "bronze_key", _fake_bronze_key)

    def _fake_read_bronze_day(source, symbol, day, granularity):
        days_called.append(day.isoformat())
        if day.isoformat() == "2024-01-01":
            raise BronzeReadError("simulated transient failure")
        return pl.DataFrame({"x": [1.0]})

    monkeypatch.setattr(bp, "read_bronze_day", _fake_read_bronze_day)
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)

    counts = pull_bronze(start="2024-01-01", end="2024-01-02", sources=["mempool"])
    assert counts["errors"] == 1
    assert counts["downloaded"] == 1
    assert "2024-01-01" in days_called
    assert "2024-01-02" in days_called


def test_pull_bronze_mempool_succeeds_on_third_attempt(monkeypatch, bronze_env):
    monkeypatch.setattr(bp, "bronze_key", lambda *a, **k: "k.parquet")
    calls = {"n": 0}

    def _fake_read_bronze_day(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise BronzeReadError("transient")
        return pl.DataFrame({"x": [1.0]})

    monkeypatch.setattr(bp, "read_bronze_day", _fake_read_bronze_day)
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)

    counts = pull_bronze(start="2024-01-01", end="2024-01-01", sources=["mempool"])
    assert counts["downloaded"] == 1
    assert counts["errors"] == 0
    assert calls["n"] == 3


def test_pull_bronze_mempool_empty_frame_counts_as_downloaded(monkeypatch, bronze_env):
    """Empty bronze frame on a real day is not an error: the day exists but
    has no mempool snapshots. Original semantics; preserved here to prevent
    silent over-counting of `errors`."""
    monkeypatch.setattr(bp, "bronze_key", lambda *a, **k: "k.parquet")

    def _fake_read_bronze_day(*args, **kwargs):
        return pl.DataFrame()  # zero rows

    monkeypatch.setattr(bp, "read_bronze_day", _fake_read_bronze_day)
    monkeypatch.setattr(bp.time, "sleep", lambda s: None)

    counts = pull_bronze(start="2024-01-01", end="2024-01-02", sources=["mempool"])
    assert counts["downloaded"] == 2
    assert counts["errors"] == 0
    assert counts["skipped"] == 0
