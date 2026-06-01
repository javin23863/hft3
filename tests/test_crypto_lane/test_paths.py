"""Tests for crypto_lane.src.ingest.paths."""
from __future__ import annotations

from pathlib import Path

import pytest

from crypto_lane.src.ingest import paths as paths_mod
from crypto_lane.src import config_loader as cfg_loader_mod


def test_data_root_falls_back_when_universe_yaml_missing(monkeypatch, tmp_path):
    """If universe.yaml lacks a paths.data_root entry, data_root() should
    return a sensible default under the repo root."""
    monkeypatch.setattr(paths_mod, "load_universe", lambda: {})
    out = paths_mod.data_root()
    assert isinstance(out, Path)
    # default is "data/crypto" relative to repo root
    assert out.name == "crypto"
    assert out.parent.name == "data"


def test_data_root_reads_universe_yaml(monkeypatch, tmp_path):
    """data_root() should compose repo_root / paths.data_root."""
    monkeypatch.setattr(
        paths_mod,
        "load_universe",
        lambda: {"paths": {"data_root": "some/custom/dir"}},
    )
    out = paths_mod.data_root()
    assert isinstance(out, Path)
    assert out.parts[-3:] == ("some", "custom", "dir")


def test_load_universe_raises_clear_error_on_missing(tmp_path):
    """load_universe(<nonexistent>) should raise a clear filesystem error,
    not silently swallow it."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises((FileNotFoundError, OSError)):
        cfg_loader_mod.load_universe(missing)


def test_ensure_data_dirs_is_idempotent(monkeypatch, tmp_path):
    """Calling ensure_data_dirs() twice should not raise and the dirs should exist."""
    fake_root = tmp_path / "data_root"
    monkeypatch.setattr(paths_mod, "data_root", lambda: fake_root)

    paths_mod.ensure_data_dirs()
    paths_mod.ensure_data_dirs()

    assert (fake_root / "bronze").is_dir()
    assert (fake_root / "normalized").is_dir()
