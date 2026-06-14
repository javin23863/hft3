from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import data_doctor  # noqa: E402
from data_doctor import catalog_coverage_detail  # noqa: E402


def _write_npz(path: Path, n: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, data=np.arange(n, dtype=np.int64))


def test_catalog_coverage_accepts_manifest_plus_quarantine(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    good = nroot / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    empty = nroot / "MES.v.0_NFP_2024_10_04_TIGHT_mbo.npz"
    _write_npz(good, 2)
    _write_npz(empty, 0)
    (nroot / "manifest.json").write_text(
        json.dumps([{"npz_path": str(good), "event_count": 2}]),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text(
        json.dumps([{"npz_path": str(empty), "event_count": 0, "error": "empty data/quotes array"}]),
        encoding="utf-8",
    )

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is True
    assert hard_failure is False
    assert "catalog=1 quarantine=1 on_disk=2" in detail
    assert "unaccounted=0" in detail


def test_catalog_coverage_warns_on_unaccounted_npz(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    good = nroot / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    stray = nroot / "MES.v.0_NFP_2024_10_04_TIGHT_mbo.npz"
    _write_npz(good, 2)
    _write_npz(stray, 2)
    (nroot / "manifest.json").write_text(
        json.dumps([{"npz_path": str(good), "event_count": 2}]),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text("[]", encoding="utf-8")

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is False
    assert "catalog=1 quarantine=0 on_disk=2" in detail
    assert "unaccounted=1" in detail


def test_catalog_coverage_rejects_wrong_catalog_paths(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    good = nroot / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    empty = nroot / "MES.v.0_NFP_2024_10_04_TIGHT_mbo.npz"
    _write_npz(good, 2)
    _write_npz(empty, 0)
    (nroot / "manifest.json").write_text(
        json.dumps([{"npz_path": str(nroot / "missing_1.npz"), "event_count": 2}]),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text(
        json.dumps([{"npz_path": str(nroot / "missing_2.npz"), "event_count": 0}]),
        encoding="utf-8",
    )

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is False
    assert "unaccounted=2" in detail
    assert "overaccounted=2" in detail


def test_catalog_coverage_hard_fails_duplicate_and_overlap(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    good = nroot / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    _write_npz(good, 2)
    (nroot / "manifest.json").write_text(
        json.dumps(
            [
                {"npz_path": str(good), "event_count": 2},
                {"npz_path": str(good), "event_count": 2},
            ]
        ),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text(
        json.dumps([{"npz_path": str(good), "event_count": 0, "error": "duplicate"}]),
        encoding="utf-8",
    )

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is True
    assert "duplicates=1" in detail
    assert "manifest_quarantine_overlap=1" in detail


def test_catalog_coverage_hard_fails_malformed_rows(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    nroot.mkdir()
    (nroot / "manifest.json").write_text(json.dumps([{"event_count": 1}]), encoding="utf-8")
    (nroot / "catalog_quarantine.json").write_text("{bad", encoding="utf-8")

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is True
    assert "manifest[0].npz_path=missing" in detail
    assert "quarantine=unreadable:JSONDecodeError" in detail


def test_catalog_coverage_hard_fails_non_npz_path(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    nroot.mkdir()
    (nroot / "manifest.json").write_text(
        json.dumps([{"npz_path": str(nroot / "bad.txt"), "event_count": 1}]),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text("[]", encoding="utf-8")

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is True
    assert "manifest[0].npz_path=not_npz" in detail


def test_catalog_coverage_hard_fails_nested_or_outside_path(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    nested = nroot / "nested" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    outside = tmp_path / "other" / "MES.v.0_NFP_2024_10_04_TIGHT_mbo.npz"
    nested.parent.mkdir(parents=True)
    outside.parent.mkdir(parents=True)
    (nroot / "manifest.json").write_text(
        json.dumps([{"npz_path": str(nested), "event_count": 1}]),
        encoding="utf-8",
    )
    (nroot / "catalog_quarantine.json").write_text(
        json.dumps([{"npz_path": str(outside), "event_count": 0}]),
        encoding="utf-8",
    )

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is True
    assert "manifest[0].npz_path=not_top_level" in detail
    assert "quarantine[0].npz_path=not_top_level" in detail


def test_catalog_coverage_hard_fails_on_malformed_manifest(tmp_path: Path) -> None:
    nroot = tmp_path / "npz"
    nroot.mkdir()
    (nroot / "manifest.json").write_text("{bad", encoding="utf-8")
    (nroot / "catalog_quarantine.json").write_text("[]", encoding="utf-8")

    ok, detail, hard_failure = catalog_coverage_detail(nroot)

    assert ok is False
    assert hard_failure is True
    assert "manifest=unreadable:JSONDecodeError" in detail


def test_main_fails_closed_on_malformed_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    nroot = tmp_path / "lake" / "npz"
    lroot = nroot.parent
    nroot.mkdir(parents=True)
    (lroot / "mbo_release").mkdir()
    (nroot / "manifest.json").write_text("{bad", encoding="utf-8")
    (nroot / "catalog_quarantine.json").write_text("[]", encoding="utf-8")
    ledger = lroot / "manifest.parquet"
    ledger.write_bytes(b"x" * 1_000_001)
    repo.mkdir()

    data_doctor.checks.clear()
    monkeypatch.setattr(data_doctor, "_REPO", repo)
    monkeypatch.setattr(data_doctor, "options_lane_checks", lambda _root: None)
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(nroot))
    monkeypatch.setenv("HFT3_MANIFEST_PATH", str(ledger))
    monkeypatch.setattr(sys, "argv", ["data_doctor.py", "--skip-b2"])

    assert data_doctor.main() == 1

    coverage = next(check for check in data_doctor.checks if check["name"] == "catalog-coverage")
    assert coverage["status"] == "FAIL"
