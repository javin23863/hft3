"""Tests for crypto_lane.src.config.data_paths."""
from __future__ import annotations

from pathlib import Path

import pytest

from crypto_lane.src.config import data_paths as data_paths_mod


def test_fixture_mode_returns_fixtures_root():
    out = data_paths_mod.resolve_lane_data_dir({"validation_mode": "fixture"})
    assert isinstance(out, Path)
    # universe.yaml ships with fixtures_root = packages/crypto_lane/fixtures
    assert out.parts[-2:] == ("crypto_lane", "fixtures")


def test_default_mode_is_fixture():
    """No validation_mode set -> default fixture path."""
    out = data_paths_mod.resolve_lane_data_dir({})
    assert out.parts[-2:] == ("crypto_lane", "fixtures")


def test_production_mode_raises_filenotfound_on_missing_data(monkeypatch, tmp_path):
    """In production mode with no required CSVs present, expect FileNotFoundError."""
    monkeypatch.setattr(
        data_paths_mod,
        "load_universe",
        lambda: {"paths": {"data_root": str(tmp_path)}},
    )
    monkeypatch.setattr(
        data_paths_mod, "repo_root_from_lane", lambda: Path("/")
    )
    with pytest.raises(FileNotFoundError):
        data_paths_mod.resolve_lane_data_dir({"validation_mode": "production"})


def test_data_provenance_source_returns_string():
    fix = data_paths_mod.data_provenance_source({"validation_mode": "fixture"})
    prod = data_paths_mod.data_provenance_source({"validation_mode": "production"})
    assert isinstance(fix, str) and fix
    assert isinstance(prod, str) and prod
    assert fix != prod
    assert "fixture" in fix
    assert "production" in prod
