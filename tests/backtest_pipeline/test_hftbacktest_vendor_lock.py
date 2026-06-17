"""Tests for vendor/hftbacktest/VENDOR.lock and install helpers."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.hftbacktest_realism import (
    default_hftbacktest_upstream_ref,
    read_hftbacktest_vendor_lock,
)


def test_vendor_lock_matches_upstream_repo_and_version() -> None:
    lock = read_hftbacktest_vendor_lock(_REPO)
    assert lock["upstream_repo_url"] == "https://github.com/nkaz001/hftbacktest"
    assert lock["upstream_commit_sha_or_tag"] == "v2.4.2"
    assert lock["python_package_version"] == "2.4.2"
    assert lock["pypi_package"] == "hftbacktest"
    assert default_hftbacktest_upstream_ref(_REPO) == "v2.4.2"
