from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_lake_catalog import _catalog_entry, _hash_and_count, main  # noqa: E402


def test_hash_and_count_accepts_nonempty_data_npz(tmp_path: Path) -> None:
    p = tmp_path / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    np.savez_compressed(p, data=np.arange(3, dtype=np.int64))

    rec = _hash_and_count(str(p))

    assert rec["event_count"] == 3
    assert "error" not in rec


def test_hash_and_count_quarantines_empty_data_npz(tmp_path: Path) -> None:
    p = tmp_path / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    np.savez_compressed(p, data=np.array([], dtype=np.int64))

    rec = _hash_and_count(str(p))

    assert rec["event_count"] == 0
    assert rec["error"] == "empty data array"


def test_hash_and_count_accepts_nonempty_quotes_npz(tmp_path: Path) -> None:
    p = tmp_path / "VIX.OPT_CPI_2024_09_11_TIGHT_quotes.npz"
    np.savez_compressed(p, quotes=np.arange(3, dtype=np.int64))

    rec = _hash_and_count(str(p))

    assert rec["event_count"] == 3
    assert "error" not in rec


def test_hash_and_count_quarantines_empty_quotes_npz(tmp_path: Path) -> None:
    p = tmp_path / "VIX.OPT_CPI_2024_09_11_TIGHT_quotes.npz"
    np.savez_compressed(p, quotes=np.array([], dtype=np.int64))

    rec = _hash_and_count(str(p))

    assert rec["event_count"] == 0
    assert rec["error"] == "empty quotes array"


def test_hash_and_count_quarantines_missing_data_or_quotes_key(tmp_path: Path) -> None:
    p = tmp_path / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    np.savez_compressed(p, other=np.arange(3, dtype=np.int64))

    rec = _hash_and_count(str(p))

    assert rec["event_count"] == 0
    assert rec["error"].startswith("no data/quotes key")


def test_catalog_entry_quarantines_cached_zero_event_count() -> None:
    rec = {
        "npz_path": "C:/hft3-lake/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "event_count": 0,
        "sha256": "a" * 64,
        "size": 123,
        "mtime": 1.0,
    }

    out, is_quarantined = _catalog_entry(rec, "2026-06-14T00:00:00+00:00")

    assert is_quarantined is True
    assert out["event_id"] == "CPI_2024_09_11_TIGHT"
    assert out["symbol"] == "MES.v.0"
    assert out["error"] == "empty data/quotes array"


def test_catalog_entry_accepts_positive_event_count() -> None:
    rec = {
        "npz_path": "C:/hft3-lake/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "event_count": 2,
        "sha256": "a" * 64,
        "size": 123,
        "mtime": 1.0,
    }

    out, is_quarantined = _catalog_entry(rec, "2026-06-14T00:00:00+00:00")

    assert is_quarantined is False
    assert out["event_count"] == 2
    assert "error" not in out


def test_catalog_entry_quarantines_invalid_cached_sha256() -> None:
    rec = {
        "npz_path": "C:/hft3-lake/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "event_count": 2,
        "sha256": "not-a-digest",
        "size": 123,
        "mtime": 1.0,
    }

    out, is_quarantined = _catalog_entry(rec, "2026-06-14T00:00:00+00:00")

    assert is_quarantined is True
    assert out["error"] == "invalid sha256"


def test_main_quarantines_cached_zero_count_without_deleting_npz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lake = tmp_path / "npz"
    lake.mkdir()
    npz = lake / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
    np.savez_compressed(npz, data=np.array([], dtype=np.int64))
    stat = npz.stat()
    (lake / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "event_id": "CPI_2024_09_11_TIGHT",
                    "symbol": "MES.v.0",
                    "npz_path": str(npz),
                    "event_count": 0,
                    "sha256": "a" * 64,
                    "created_utc": "2026-06-14T00:00:00+00:00",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(lake))
    monkeypatch.setattr(sys, "argv", ["build_lake_catalog.py"])

    assert main() == 0

    records = json.loads((lake / "manifest.json").read_text(encoding="utf-8"))
    quarantine = json.loads((lake / "catalog_quarantine.json").read_text(encoding="utf-8"))
    assert records == []
    assert len(quarantine) == 1
    assert quarantine[0]["event_count"] == 0
    assert quarantine[0]["error"] == "empty data/quotes array"
    assert npz.is_file()
