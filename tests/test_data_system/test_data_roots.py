"""Tests for paid data root discovery and multi-root NPZ resolution."""

from __future__ import annotations

import json
from pathlib import Path

from data_system.src.data_roots import npz_search_dirs, paid_data_root, verify_data_for_event
from data_system.src.npz_resolver import resolve_npz_for_event


def test_npz_search_dirs_includes_paid_root(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    paid = tmp_path / "paid_store" / "data"
    (repo / "data" / "npz").mkdir(parents=True)
    (paid / "npz").mkdir(parents=True)
    monkeypatch.setenv("HFT3_PAID_DATA_ROOT", str(paid))
    dirs = npz_search_dirs(repo)
    assert dirs[0] == (repo / "data" / "npz").resolve()
    assert (paid / "npz").resolve() in dirs


def test_resolve_npz_finds_file_in_paid_root_only(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    paid = tmp_path / "paid_store" / "data"
    repo.mkdir()
    paid.mkdir(parents=True)
    monkeypatch.setenv("HFT3_PAID_DATA_ROOT", str(paid))

    event_id = "NFP_2024_01_05_TIGHT"
    npz_path = paid / "npz" / f"NQ.v.0_{event_id}_mbo.npz"
    npz_path.parent.mkdir(parents=True, exist_ok=True)
    npz_path.write_bytes(b"npz")

    path, present, sym_used = resolve_npz_for_event(repo, event_id, "NQ.v.0", ("NQ.v.0",))
    assert present
    assert sym_used == "NQ.v.0"
    assert path == npz_path.resolve()


def test_paid_data_root_from_manifest(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    paid_repo = tmp_path / "paid_repo"
    paid_data = paid_repo / "data"
    (paid_data / "npz").mkdir(parents=True)
    manifest_dir = repo / "packages" / "data_system" / "config"
    manifest_dir.mkdir(parents=True)
    manifest = {
        "repository_root": str(paid_repo),
        "local_paths": {"runnable_npz_dir": "data/npz/"},
    }
    (manifest_dir / "mbo_pilot_basket_20260605_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.delenv("HFT3_PAID_DATA_ROOT", raising=False)
    assert paid_data_root(repo).resolve() == paid_data.resolve()


def test_verify_data_for_event_fail_closed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HFT3_PAID_DATA_ROOT", str(tmp_path / "empty" / "data"))
    result = verify_data_for_event(repo, "CPI_2024_09_11_TIGHT", "NQ.v.0", ("NQ.v.0",))
    assert result["ok"] is False
    assert "sync_command" in result
