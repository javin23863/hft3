"""Manifest v2 upsert and migration tests."""
from __future__ import annotations

import json
from pathlib import Path


def test_manifest_v2_upsert_merge(tmp_path: Path):
    from equities_lane.src.ingest.manifest_v2 import load_manifest_v2, upsert_session, write_manifest_v2

    path = tmp_path / "session_bundle_v2.json"
    manifest = {"schema_version": 2, "sessions": []}
    upsert_session(
        manifest,
        {
            "session_id": "gme_2021",
            "underlying": "GME",
            "equity": {"raw_path": "/a"},
        },
    )
    upsert_session(
        manifest,
        {
            "session_id": "gme_2021",
            "options": {"normalized_path": "/b"},
        },
    )
    write_manifest_v2(path, manifest)
    loaded = load_manifest_v2(path)
    row = loaded["sessions"][0]
    assert row["equity"]["raw_path"] == "/a"
    assert row["options"]["normalized_path"] == "/b"


def test_migrate_v1_row():
    from equities_lane.src.ingest.manifest_v2 import migrate_v1_row

    row = migrate_v1_row(
        {
            "session_id": "gme_2021",
            "symbol": "GME",
            "date": "2021-01-27",
            "status": "pulled",
            "raw_path": "/raw",
        }
    )
    assert row["session_id"] == "gme_2021"
    assert row["equity"]["raw_path"] == "/raw"
