"""Tests for options_lane.studies.oi_decode — synthetic fixtures only.

All tests use monkeypatched DBNStore so no real .dbn.zst files are required.
Covers:
  - OI stat_type filtering (only stat_type==9 kept)
  - update_action filtering (only action==1/NEW kept)
  - last-wins deduplication on (instrument_id, ts_ref_date)
  - empty input (no records) -> empty arrays
  - load_oi_dir with missing directory -> empty arrays
  - load_oi_dir concatenation across multiple synthetic files
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

from options_lane.studies.oi_decode import load_oi_from_dbn, load_oi_dir

# ---------------------------------------------------------------------------
# Synthetic record builder
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_OI_STAT_TYPE = 9   # StatType.OPEN_INTEREST
_NEW_ACTION   = 1   # StatUpdateAction.NEW
_DELETE_ACTION = 2  # StatUpdateAction.DELETE


def _make_stat_rec(
    instrument_id: int,
    stat_type: int,
    update_action: int,
    quantity: int,
    ts_ref_date: date,
) -> SimpleNamespace:
    """Build a minimal fake StatMsg-like object."""
    ts_ref_ns = int(datetime(
        ts_ref_date.year, ts_ref_date.month, ts_ref_date.day,
        tzinfo=_UTC
    ).timestamp() * 1_000_000_000)
    return SimpleNamespace(
        instrument_id=instrument_id,
        stat_type=stat_type,
        update_action=update_action,
        quantity=quantity,
        price=9_223_372_036_854_775_807,   # INT64_MAX sentinel — must be ignored
        ts_ref=ts_ref_ns,
        ts_event=ts_ref_ns + 1_000_000,    # arbitrary; must NOT be used for date
    )


def _patch_dbn_store(records: list, path: str | Path = "/fake/path.dbn.zst"):
    """Context-manager: monkeypatch databento.DBNStore.from_file to yield `records`."""
    mock_store = MagicMock()
    mock_store.__iter__ = MagicMock(return_value=iter(records))

    patcher = patch("databento.DBNStore.from_file", return_value=mock_store)
    return patcher


# ---------------------------------------------------------------------------
# Test 1: OI records are correctly decoded
# ---------------------------------------------------------------------------

class TestOiFiltered:
    """load_oi_from_dbn keeps only stat_type==9 AND update_action==1."""

    def test_single_oi_record_decoded(self, tmp_path):
        """One OI/NEW record -> one row with correct instrument_id and oi."""
        d = date(2023, 5, 15)
        rec = _make_stat_rec(42, _OI_STAT_TYPE, _NEW_ACTION, 1500, d)
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store([rec], fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 1
        assert int(result["instrument_id"][0]) == 42
        assert int(result["oi"][0]) == 1500
        assert result["ts_ref_date"][0] == d

    def test_multiple_oi_records(self, tmp_path):
        """Multiple distinct OI/NEW records all returned."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(1, _OI_STAT_TYPE, _NEW_ACTION, 100, d),
            _make_stat_rec(2, _OI_STAT_TYPE, _NEW_ACTION, 200, d),
            _make_stat_rec(3, _OI_STAT_TYPE, _NEW_ACTION, 300, d),
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 3
        ids = set(int(x) for x in result["instrument_id"])
        assert ids == {1, 2, 3}

    def test_price_field_is_ignored(self, tmp_path):
        """The price=INT64_MAX sentinel must not appear in output (oi comes from quantity)."""
        d = date(2023, 5, 15)
        rec = _make_stat_rec(7, _OI_STAT_TYPE, _NEW_ACTION, 999, d)
        # Sanity-check that rec.price is INT64_MAX
        assert rec.price == 9_223_372_036_854_775_807
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store([rec], fake_file):
            result = load_oi_from_dbn(fake_file)

        # oi must be 999 (from quantity), not anything related to the price sentinel
        assert int(result["oi"][0]) == 999


# ---------------------------------------------------------------------------
# Test 2: Non-OI stat types excluded
# ---------------------------------------------------------------------------

class TestNonOiExcluded:
    """Records with stat_type != 9 must be dropped entirely."""

    def test_non_oi_stat_types_dropped(self, tmp_path):
        """stat_type 1, 2, 5, 10 must not appear in results."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(10, 1,  _NEW_ACTION, 5000, d),  # opening_price
            _make_stat_rec(11, 2,  _NEW_ACTION, 5010, d),  # closing_price
            _make_stat_rec(12, 5,  _NEW_ACTION, 5020, d),  # settlement_price
            _make_stat_rec(13, 10, _NEW_ACTION,  200, d),  # some other type
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 0

    def test_mixed_keeps_only_oi(self, tmp_path):
        """Mix of OI and non-OI records: only OI rows survive."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(20, 1,            _NEW_ACTION, 5000, d),  # non-OI
            _make_stat_rec(21, _OI_STAT_TYPE, _NEW_ACTION, 777,  d),  # OI -> keep
            _make_stat_rec(22, 5,            _NEW_ACTION, 5010, d),  # non-OI
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 1
        assert int(result["instrument_id"][0]) == 21
        assert int(result["oi"][0]) == 777


# ---------------------------------------------------------------------------
# Test 3: update_action filtering
# ---------------------------------------------------------------------------

class TestUpdateActionFiltering:
    """Only update_action==NEW(1) kept; DELETE(2) and others dropped."""

    def test_delete_action_excluded(self, tmp_path):
        """A DELETE record for an OI stat must be dropped."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(30, _OI_STAT_TYPE, _DELETE_ACTION, 1234, d),
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 0

    def test_other_action_excluded(self, tmp_path):
        """An action other than 1 or 2 (e.g., 3) must be dropped."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(31, _OI_STAT_TYPE, 3, 5678, d),
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 0

    def test_new_action_kept_delete_dropped(self, tmp_path):
        """For same instrument, a NEW kept and a DELETE dropped."""
        d = date(2023, 5, 15)
        recs = [
            _make_stat_rec(32, _OI_STAT_TYPE, _NEW_ACTION,    888, d),
            _make_stat_rec(33, _OI_STAT_TYPE, _DELETE_ACTION, 999, d),
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 1
        assert int(result["instrument_id"][0]) == 32
        assert int(result["oi"][0]) == 888


# ---------------------------------------------------------------------------
# Test 4: Last-wins deduplication
# ---------------------------------------------------------------------------

class TestLastWinsDedupe:
    """When (instrument_id, ts_ref_date) repeats, last NEW wins."""

    def test_two_new_same_key_last_wins(self, tmp_path):
        """Two NEW records for same (iid, date): second (higher OI) wins."""
        d = date(2023, 5, 17)
        recs = [
            _make_stat_rec(50, _OI_STAT_TYPE, _NEW_ACTION, 1000, d),
            _make_stat_rec(50, _OI_STAT_TYPE, _NEW_ACTION, 2500, d),  # last wins
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        # Should be 1 unique row, not 2
        assert len(result["instrument_id"]) == 1
        assert int(result["oi"][0]) == 2500

    def test_different_dates_not_deduped(self, tmp_path):
        """Same iid on two different ts_ref dates -> two distinct rows."""
        d1 = date(2023, 5, 16)
        d2 = date(2023, 5, 17)
        recs = [
            _make_stat_rec(60, _OI_STAT_TYPE, _NEW_ACTION, 1000, d1),
            _make_stat_rec(60, _OI_STAT_TYPE, _NEW_ACTION, 1100, d2),
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 2

    def test_three_updates_last_wins(self, tmp_path):
        """Three NEW records for same key; only the last quantity is kept."""
        d = date(2023, 5, 18)
        recs = [
            _make_stat_rec(70, _OI_STAT_TYPE, _NEW_ACTION, 100, d),
            _make_stat_rec(70, _OI_STAT_TYPE, _NEW_ACTION, 200, d),
            _make_stat_rec(70, _OI_STAT_TYPE, _NEW_ACTION, 300, d),  # last
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 1
        assert int(result["oi"][0]) == 300

    def test_mixed_instruments_dedupe_per_key(self, tmp_path):
        """Dedup is per (iid, date) key; different instruments not conflated."""
        d = date(2023, 5, 18)
        recs = [
            _make_stat_rec(80, _OI_STAT_TYPE, _NEW_ACTION, 100, d),
            _make_stat_rec(81, _OI_STAT_TYPE, _NEW_ACTION, 200, d),
            _make_stat_rec(80, _OI_STAT_TYPE, _NEW_ACTION, 150, d),  # update iid=80
        ]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 2
        iid_to_oi = dict(zip(
            [int(x) for x in result["instrument_id"]],
            [int(x) for x in result["oi"]],
        ))
        assert iid_to_oi[80] == 150  # last update
        assert iid_to_oi[81] == 200  # unchanged


# ---------------------------------------------------------------------------
# Test 5: Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    """No records -> empty arrays with correct dtypes."""

    def test_no_records_empty_arrays(self, tmp_path):
        """An empty store returns arrays of length 0 with correct dtypes."""
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store([], fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 0
        assert len(result["ts_ref_date"]) == 0
        assert len(result["oi"]) == 0
        assert result["instrument_id"].dtype == np.int64
        assert result["oi"].dtype == np.int64

    def test_only_non_oi_records_empty(self, tmp_path):
        """Store with only non-OI stats returns empty arrays."""
        d = date(2023, 5, 15)
        recs = [_make_stat_rec(1, 1, _NEW_ACTION, 5000, d)]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["instrument_id"]) == 0

    def test_file_not_found_raises(self):
        """load_oi_from_dbn raises FileNotFoundError for nonexistent path."""
        with pytest.raises(FileNotFoundError):
            load_oi_from_dbn(Path("/nonexistent/path.dbn.zst"))


# ---------------------------------------------------------------------------
# Test 6: load_oi_dir
# ---------------------------------------------------------------------------

class TestLoadOiDir:
    """load_oi_dir behaviour: missing dir, empty dir, multi-file concat."""

    def test_missing_dir_returns_empty(self, tmp_path):
        """A directory that doesn't exist returns empty arrays."""
        missing = tmp_path / "no_such_dir"
        result = load_oi_dir(missing)
        assert len(result["instrument_id"]) == 0
        assert result["instrument_id"].dtype == np.int64

    def test_empty_dir_no_dbn_files_returns_empty(self, tmp_path):
        """A directory with no .dbn.zst files returns empty arrays."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = load_oi_dir(empty_dir)
        assert len(result["instrument_id"]) == 0

    def test_dir_with_dbn_files_concatenated(self, tmp_path):
        """Two .dbn.zst files in a dir are concatenated correctly."""
        stats_dir = tmp_path / "stats"
        stats_dir.mkdir()
        f1 = stats_dir / "month1.dbn.zst"
        f2 = stats_dir / "month2.dbn.zst"
        f1.touch()
        f2.touch()

        d1 = date(2023, 4, 28)
        d2 = date(2023, 5, 15)

        recs1 = [
            _make_stat_rec(100, _OI_STAT_TYPE, _NEW_ACTION, 1000, d1),
            _make_stat_rec(101, _OI_STAT_TYPE, _NEW_ACTION, 2000, d1),
        ]
        recs2 = [
            _make_stat_rec(102, _OI_STAT_TYPE, _NEW_ACTION, 3000, d2),
        ]

        call_count = [0]
        files_list = sorted(stats_dir.rglob("*.dbn.zst"))

        def fake_from_file(path_str):
            idx = call_count[0]
            call_count[0] += 1
            records = recs1 if idx == 0 else recs2
            mock_store = MagicMock()
            mock_store.__iter__ = MagicMock(return_value=iter(records))
            return mock_store

        with patch("databento.DBNStore.from_file", side_effect=fake_from_file):
            result = load_oi_dir(stats_dir)

        assert len(result["instrument_id"]) == 3
        ids = set(int(x) for x in result["instrument_id"])
        assert ids == {100, 101, 102}

    def test_dir_last_wins_across_files(self, tmp_path):
        """Cross-file last-wins: second file's record for same key overwrites first."""
        stats_dir = tmp_path / "stats"
        stats_dir.mkdir()
        f1 = stats_dir / "a_file.dbn.zst"
        f2 = stats_dir / "b_file.dbn.zst"
        f1.touch()
        f2.touch()

        d = date(2023, 5, 18)
        recs1 = [_make_stat_rec(200, _OI_STAT_TYPE, _NEW_ACTION, 500, d)]
        recs2 = [_make_stat_rec(200, _OI_STAT_TYPE, _NEW_ACTION, 999, d)]  # overwrites

        call_count = [0]

        def fake_from_file(path_str):
            idx = call_count[0]
            call_count[0] += 1
            records = recs1 if idx == 0 else recs2
            mock_store = MagicMock()
            mock_store.__iter__ = MagicMock(return_value=iter(records))
            return mock_store

        with patch("databento.DBNStore.from_file", side_effect=fake_from_file):
            result = load_oi_dir(stats_dir)

        assert len(result["instrument_id"]) == 1
        assert int(result["oi"][0]) == 999


# ---------------------------------------------------------------------------
# Test 7: ts_ref_date uses ts_ref field (not ts_event)
# ---------------------------------------------------------------------------

class TestTsRefDateField:
    """ts_ref (not ts_event) must be used for the session date."""

    def test_date_comes_from_ts_ref_not_ts_event(self, tmp_path):
        """ts_ref_date must reflect ts_ref, even when ts_event is different day."""
        # Build a record where ts_ref is 2023-05-18 (midnight UTC) but
        # ts_event is 2023-05-19 (one day later).
        iid = 99
        ref_date = date(2023, 5, 18)
        ts_ref_ns = int(datetime(2023, 5, 18, tzinfo=timezone.utc).timestamp() * 1e9)
        ts_event_ns = int(datetime(2023, 5, 19, tzinfo=timezone.utc).timestamp() * 1e9)

        rec = SimpleNamespace(
            instrument_id=iid,
            stat_type=_OI_STAT_TYPE,
            update_action=_NEW_ACTION,
            quantity=12345,
            price=9_223_372_036_854_775_807,
            ts_ref=ts_ref_ns,
            ts_event=ts_event_ns,
        )
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store([rec], fake_file):
            result = load_oi_from_dbn(fake_file)

        assert len(result["ts_ref_date"]) == 1
        assert result["ts_ref_date"][0] == ref_date, (
            f"Expected {ref_date}, got {result['ts_ref_date'][0]} — "
            "ts_event used instead of ts_ref"
        )


# ---------------------------------------------------------------------------
# Test 8: Output array dtypes
# ---------------------------------------------------------------------------

class TestOutputDtypes:
    """instrument_id -> int64; oi -> int64; ts_ref_date -> object (date)."""

    def test_dtypes_on_non_empty_result(self, tmp_path):
        d = date(2023, 5, 15)
        recs = [_make_stat_rec(1, _OI_STAT_TYPE, _NEW_ACTION, 100, d)]
        fake_file = tmp_path / "stats.dbn.zst"
        fake_file.touch()

        with _patch_dbn_store(recs, fake_file):
            result = load_oi_from_dbn(fake_file)

        assert result["instrument_id"].dtype == np.int64
        assert result["oi"].dtype == np.int64
        assert result["ts_ref_date"].dtype == object
        assert isinstance(result["ts_ref_date"][0], date)
