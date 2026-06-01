"""B2 client hardening tests (Phase B-1: atomic downloads + retry)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import crypto_lane.src.ingest.b2_client as b2c
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError


def _make_client_with_bucket(monkeypatch, *, side_effects):
    """Create a B2Client instance whose underlying bucket is monkeypatched.

    `side_effects` is a list of either:
      - a callable that builds a fake DownloadedFile (one per attempt)
      - an Exception instance to raise (e.g. B2ClientError("boom"))
    Each call to `bucket.download_file_by_name` consumes one entry.
    """
    client = B2Client.__new__(B2Client)
    client._api = type("FakeApi", (), {})()
    calls = {"n": 0}

    class _FakeBucket:
        def download_file_by_name(self, key):
            idx = calls["n"]
            calls["n"] += 1
            item = side_effects[idx]
            if isinstance(item, BaseException):
                raise item
            return item(key)

    client._api.get_bucket_by_name = lambda name: _FakeBucket()
    return client, calls


def test_download_to_path_uses_atomic_replace(monkeypatch, tmp_path):
    parent = tmp_path / "lake"
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / "bronze.parquet"

    def _make_downloaded(key):
        class _Downloaded:
            def save_to(self, path):
                with open(path, "wb") as f:
                    f.write(b"PAYLOAD")
        return _Downloaded()

    monkeypatch.setattr(b2c.time, "sleep", lambda s: None)
    client, _calls = _make_client_with_bucket(monkeypatch, side_effects=[_make_downloaded])

    out = client.download_to_path("bucket", "k", dest)
    assert out == dest
    assert dest.is_file()
    assert dest.read_bytes() == b"PAYLOAD"

    leftovers = [p for p in parent.iterdir() if p.name != dest.name]
    assert leftovers == [], f"orphan tempfiles: {leftovers}"


def test_download_to_path_retries_on_transient_error(monkeypatch, tmp_path):
    dest = tmp_path / "x.parquet"

    def _ok(key):
        class _Downloaded:
            def save_to(self, path):
                with open(path, "wb") as f:
                    f.write(b"data")
        return _Downloaded()

    sleeps: list[float] = []
    monkeypatch.setattr(b2c.time, "sleep", lambda s: sleeps.append(s))
    client, calls = _make_client_with_bucket(
        monkeypatch,
        side_effects=[B2ClientError("boom1"), B2ClientError("boom2"), _ok],
    )

    out = client.download_to_path("bucket", "k", dest)
    assert out == dest
    assert dest.is_file()
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_download_to_path_propagates_programmer_error(monkeypatch, tmp_path):
    dest = tmp_path / "x.parquet"

    sleeps: list[float] = []
    monkeypatch.setattr(b2c.time, "sleep", lambda s: sleeps.append(s))
    client, calls = _make_client_with_bucket(monkeypatch, side_effects=[KeyError("bad key")])

    # Programmer errors (KeyError, AttributeError, etc.) must propagate
    # unwrapped so the caller sees the real class. Only b2sdk business
    # errors and retryable transport errors get re-wrapped as B2ClientError.
    with pytest.raises(KeyError):
        client.download_to_path("bucket", "k", dest)
    assert calls["n"] == 1
    assert sleeps == []


def test_download_to_path_cleans_up_tempfile_on_failure(monkeypatch, tmp_path):
    parent = tmp_path / "lake"
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / "broken.parquet"

    def _bad(key):
        class _Downloaded:
            def save_to(self, path):
                with open(path, "wb") as f:
                    f.write(b"partial")
                raise OSError("disk full mid-write")
        return _Downloaded()

    monkeypatch.setattr(b2c.time, "sleep", lambda s: None)
    # 3 attempts so _with_retry actually exhausts before the outer catch wraps.
    client, calls = _make_client_with_bucket(
        monkeypatch, side_effects=[_bad, _bad, _bad]
    )

    with pytest.raises(B2ClientError):
        client.download_to_path("bucket", "k", dest)

    assert calls["n"] == 3
    assert not dest.exists()
    leftovers = [p for p in parent.iterdir() if p.name != dest.name]
    assert leftovers == [], f"orphan tempfiles: {leftovers}"


def test_download_bytes_retries_then_succeeds(monkeypatch):
    def _ok(key):
        class _Downloaded:
            def save(self, buf):
                buf.write(b"BYTES")
        return _Downloaded()

    sleeps: list[float] = []
    monkeypatch.setattr(b2c.time, "sleep", lambda s: sleeps.append(s))
    client, calls = _make_client_with_bucket(
        monkeypatch,
        side_effects=[ConnectionError("net1"), TimeoutError("net2"), _ok],
    )

    out = client.download_bytes("bucket", "k")
    assert out == b"BYTES"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]
