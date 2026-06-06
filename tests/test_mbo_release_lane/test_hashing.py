"""Deterministic hashing for MBO lane artifacts."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from mbo_release_lane.hashing import sha256_file, sha256_json_payload


def test_json_hash_deterministic():
    payload = {"a": 1, "b": [2, 3]}
    assert sha256_json_payload(payload) == sha256_json_payload({"b": [2, 3], "a": 1})


def test_file_hash_stable(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"x":1}\n', encoding="utf-8")
    h1 = sha256_file(p)
    h2 = sha256_file(p)
    assert h1 == h2
    p.write_text('{"x":2}\n', encoding="utf-8")
    assert sha256_file(p) != h1
