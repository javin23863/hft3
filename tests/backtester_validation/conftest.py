"""Shared fixtures for backtester validation tiers."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE_NPZ = _REPO / "tests" / "fixtures" / "replay_minimal_mbo.npz"


@pytest.fixture(scope="session", autouse=True)
def _replay_execution_mode() -> None:
    os.environ.setdefault("EXECUTION_MODE", "REPLAY")


@pytest.fixture(scope="module")
def minimal_npz() -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    if not _FIXTURE_NPZ.is_file():
        build_minimal_mbo_npz(_FIXTURE_NPZ)
    return _FIXTURE_NPZ


@pytest.fixture
def minimal_npz_tmp(tmp_path: Path) -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    p = tmp_path / "minimal_mbo.npz"
    build_minimal_mbo_npz(p)
    return p
