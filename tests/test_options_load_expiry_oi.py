"""Tests for load_expiry_oi, load_expiry_oi_split, classify_heavy_light, OIStore.

All tests use synthetic stores built from fake OI arrays + fake def maps.
No real .dbn.zst files are required.

Filtration F_t — NO LOOKAHEAD:
    A larger OI value is deliberately planted on the expiry date D itself AND
    on D+1 to verify that load_expiry_oi ignores both and returns only the T-1
    (prior session) value.  Any implementation that reads the wrong date will
    fail the no-lookahead assertion.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

from options_lane.studies.fixing_window_study import (
    OIStore,
    classify_heavy_light,
    load_expiry_oi,
    load_expiry_oi_split,
)
from options_lane.studies.definition_map import OptionMeta

# ---------------------------------------------------------------------------
# Helpers to build synthetic stores
# ---------------------------------------------------------------------------

_EXPIRY = datetime.date(2023, 5, 19)
_T_MINUS_1 = datetime.date(2023, 5, 18)   # prior session — the correct answer
_T_MINUS_2 = datetime.date(2023, 5, 17)   # earlier session
_T_PLUS_0 = _EXPIRY                        # lookahead: expiry day itself
_T_PLUS_1 = datetime.date(2023, 5, 20)    # lookahead: day after expiry


def _make_def_map(
    call_ids: list[int],
    put_ids: list[int],
    expiry: datetime.date = _EXPIRY,
) -> dict[int, OptionMeta]:
    """Build a synthetic definition map."""
    result: dict[int, OptionMeta] = {}
    for iid in call_ids:
        result[iid] = OptionMeta(
            expiration=expiry,
            strike=4500.0,
            right="C",
            underlying="ESM3",
            root_asset="EW3",
            raw_symbol=f"EW3K3 C4500 id={iid}",
        )
    for iid in put_ids:
        result[iid] = OptionMeta(
            expiration=expiry,
            strike=4500.0,
            right="P",
            underlying="ESM3",
            root_asset="EW3",
            raw_symbol=f"EW3K3 P4500 id={iid}",
        )
    return result


def _make_oi_arrays(
    rows: list[tuple[datetime.date, int, int]],
) -> dict:
    """Build synthetic OI arrays from (ts_ref_date, instrument_id, oi) tuples."""
    if not rows:
        return {
            "ts_ref_date": np.empty(0, dtype=object),
            "instrument_id": np.empty(0, dtype=np.int64),
            "oi": np.empty(0, dtype=np.int64),
        }
    dates, iids, ois = zip(*rows)
    return {
        "ts_ref_date": np.array(dates, dtype=object),
        "instrument_id": np.array(iids, dtype=np.int64),
        "oi": np.array(ois, dtype=np.int64),
    }


def _make_store(
    call_ids: list[int],
    put_ids: list[int],
    oi_rows: list[tuple[datetime.date, int, int]],
    expiry: datetime.date = _EXPIRY,
) -> OIStore:
    """Construct an OIStore with pre-built synthetic def map and OI arrays.

    Bypasses all disk I/O so CI without the lake works fine.
    """
    store = OIStore.__new__(OIStore)
    store._options_root = None
    store._root_asset = "EW3"
    store._loaded = True  # mark as already loaded
    store._def_map = _make_def_map(call_ids, put_ids, expiry)
    store._oi = _make_oi_arrays(oi_rows)
    return store


# ---------------------------------------------------------------------------
# Test: returns None when store has no data
# ---------------------------------------------------------------------------

class TestLoadExpiry_Empty:
    def test_empty_def_map(self):
        """store with no def map instruments -> None."""
        store = _make_store([], [], [])
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result is None

    def test_empty_oi_arrays(self):
        """def map exists but no OI rows -> None."""
        store = _make_store([101, 102], [201, 202], [])
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result is None

    def test_unready_store(self):
        """store._loaded = False -> ready == False -> None returned."""
        store = OIStore.__new__(OIStore)
        store._options_root = None
        store._root_asset = "EW3"
        store._loaded = False
        store._def_map = None
        store._oi = None
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result is None

    def test_no_instruments_expiring_on_date(self):
        """No instruments expire on the queried date -> None."""
        # Instruments expire 2023-05-12 (different expiry)
        other_expiry = datetime.date(2023, 5, 12)
        store = _make_store(
            [101], [201],
            [(_T_MINUS_1, 101, 500), (_T_MINUS_1, 201, 500)],
            expiry=other_expiry,
        )
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Test: NO-LOOKAHEAD — T-1 value is returned, D and D+1 are ignored
# ---------------------------------------------------------------------------

class TestNoLookahead:
    """Explicitly verify that load_expiry_oi returns the T-1 (prior session)
    value and ignores any OI on D itself or D+1.

    Seed values:
        T-2  (2023-05-17): 600_000  (earlier, not the latest T-1)
        T-1  (2023-05-18): 1_000_000  <- CORRECT answer
        D    (2023-05-19): 9_999_999  <- LOOKAHEAD — must NOT be used
        D+1  (2023-05-20): 99_999_999 <- FUTURE LOOKAHEAD — must NOT be used
    """

    def _make_full_store(self) -> OIStore:
        call_ids = [101, 102]
        put_ids  = [201, 202]
        oi_rows = [
            # T-2 (earlier session)
            (_T_MINUS_2, 101, 150_000),
            (_T_MINUS_2, 102, 150_000),
            (_T_MINUS_2, 201, 150_000),
            (_T_MINUS_2, 202, 150_000),  # sum T-2 = 600_000
            # T-1 (prior session — correct)
            (_T_MINUS_1, 101, 250_000),
            (_T_MINUS_1, 102, 250_000),
            (_T_MINUS_1, 201, 250_000),
            (_T_MINUS_1, 202, 250_000),  # sum T-1 = 1_000_000
            # D (expiry day — lookahead; must be ignored)
            (_T_PLUS_0, 101, 2_000_000),
            (_T_PLUS_0, 102, 2_000_000),
            (_T_PLUS_0, 201, 2_999_999),
            (_T_PLUS_0, 202, 2_999_999),  # sum D = 9_999_998 (not exact — doesn't matter)
            # D+1 (future — must be ignored)
            (_T_PLUS_1, 101, 25_000_000),
            (_T_PLUS_1, 102, 25_000_000),
        ]
        return _make_store(call_ids, put_ids, oi_rows)

    def test_returns_t_minus_1_not_d(self):
        """load_expiry_oi must return T-1 OI (1_000_000), not D or D+1 OI."""
        store = self._make_full_store()
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result is not None, "Expected non-None OI for expiry date with T-1 data"
        assert result == 1_000_000.0, (
            f"Expected T-1 OI=1_000_000, got {result!r} — possible lookahead!"
        )

    def test_not_equal_to_d_value(self):
        """Explicitly assert result != D's seeded value to catch lookahead bugs."""
        store = self._make_full_store()
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        d_oi_sum = 2_000_000 + 2_000_000 + 2_999_999 + 2_999_999
        assert result != d_oi_sum, (
            f"result == {result!r} == D's own OI ({d_oi_sum}) — LOOKAHEAD BUG!"
        )

    def test_not_equal_to_d_plus_1_value(self):
        """Explicitly assert result != D+1 seeded value."""
        store = self._make_full_store()
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        d1_partial = 25_000_000 + 25_000_000
        assert result != d1_partial, (
            f"result == {result!r} uses D+1 data — LOOKAHEAD BUG!"
        )

    def test_t_minus_2_not_returned_when_t_minus_1_exists(self):
        """Confirm T-2 is not returned when T-1 is available (latest prior wins)."""
        store = self._make_full_store()
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        t2_sum = 600_000.0
        assert result != t2_sum, (
            f"result == {result!r} — returned T-2 instead of T-1"
        )

    def test_only_t_minus_2_available_returns_it(self):
        """When only T-2 data exists (no T-1), T-2 is the latest prior and valid."""
        call_ids = [101]
        put_ids  = [201]
        oi_rows = [
            (_T_MINUS_2, 101, 300),
            (_T_MINUS_2, 201, 300),
            # no T-1 records
        ]
        store = _make_store(call_ids, put_ids, oi_rows)
        result = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result == 600.0

    def test_accepts_date_object(self):
        """load_expiry_oi should also accept a plain datetime.date."""
        store = self._make_full_store()
        result = load_expiry_oi(_EXPIRY, store=store)
        assert result == 1_000_000.0


# ---------------------------------------------------------------------------
# Test: load_expiry_oi_split
# ---------------------------------------------------------------------------

class TestLoadExpiry_Split:
    def _make_split_store(self) -> OIStore:
        call_ids = [101, 102]  # calls: 300k + 200k = 500k
        put_ids  = [201, 202]  # puts:  150k + 350k = 500k
        oi_rows = [
            (_T_MINUS_1, 101, 300_000),
            (_T_MINUS_1, 102, 200_000),
            (_T_MINUS_1, 201, 150_000),
            (_T_MINUS_1, 202, 350_000),
            # D (lookahead — must be ignored)
            (_T_PLUS_0, 101, 9_000_000),
            (_T_PLUS_0, 201, 9_000_000),
        ]
        return _make_store(call_ids, put_ids, oi_rows)

    def test_split_sums_to_total(self):
        """calls_oi + puts_oi must equal total_oi."""
        store = self._make_split_store()
        calls, puts, total = load_expiry_oi_split(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert calls is not None
        assert puts is not None
        assert total is not None
        assert abs(calls + puts - total) < 1e-6, (
            f"calls={calls} + puts={puts} != total={total}"
        )

    def test_split_values(self):
        """Calls and puts match seeded T-1 values; not D's lookahead values."""
        store = self._make_split_store()
        calls, puts, total = load_expiry_oi_split(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert calls == 500_000.0
        assert puts == 500_000.0
        assert total == 1_000_000.0

    def test_split_no_lookahead(self):
        """split must not include D's seeded 9_000_000 values."""
        store = self._make_split_store()
        calls, puts, total = load_expiry_oi_split(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert total is not None
        assert total < 9_000_000, f"split total={total} looks like lookahead"

    def test_split_empty_store(self):
        """Empty store -> (None, None, None)."""
        store = _make_store([], [], [])
        result = load_expiry_oi_split(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert result == (None, None, None)

    def test_split_matches_total_function(self):
        """total from split must match scalar load_expiry_oi."""
        store = self._make_split_store()
        _, _, total_split = load_expiry_oi_split(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        scalar = load_expiry_oi(
            datetime.datetime(2023, 5, 19, 20, 0, tzinfo=datetime.timezone.utc),
            store=store,
        )
        assert total_split == scalar


# ---------------------------------------------------------------------------
# Test: classify_heavy_light
# ---------------------------------------------------------------------------

class TestClassifyHeavyLight:
    def test_above_median_is_heavy(self):
        ref = [100.0, 200.0, 300.0, 400.0, 500.0]  # median = 300
        assert classify_heavy_light(400.0, ref) == "heavy"
        assert classify_heavy_light(500.0, ref) == "heavy"
        assert classify_heavy_light(301.0, ref) == "heavy"

    def test_at_median_is_light(self):
        ref = [100.0, 200.0, 300.0, 400.0, 500.0]  # median = 300
        # at-or-below median -> light
        assert classify_heavy_light(300.0, ref) == "light"
        assert classify_heavy_light(299.0, ref) == "light"
        assert classify_heavy_light(100.0, ref) == "light"

    def test_below_median_is_light(self):
        ref = [1_000_000.0, 2_000_000.0]  # median = 1_500_000
        assert classify_heavy_light(500_000.0, ref) == "light"

    def test_none_input_returns_none(self):
        assert classify_heavy_light(None, [100.0, 200.0]) is None

    def test_empty_ref_returns_none(self):
        assert classify_heavy_light(100.0, []) is None

    def test_numpy_array_ref(self):
        """ref_distribution can be a numpy array."""
        ref = np.array([100.0, 300.0, 500.0])  # median = 300
        assert classify_heavy_light(400.0, ref) == "heavy"
        assert classify_heavy_light(200.0, ref) == "light"

    def test_single_element_ref(self):
        """Single-element ref: median == that element; above=heavy, at/below=light."""
        assert classify_heavy_light(1001.0, [1000.0]) == "heavy"
        assert classify_heavy_light(1000.0, [1000.0]) == "light"
        assert classify_heavy_light(999.0, [1000.0]) == "light"


# ---------------------------------------------------------------------------
# Test: OIStore.ready property
# ---------------------------------------------------------------------------

class TestOIStoreReady:
    def test_empty_def_map_not_ready(self):
        store = _make_store([], [], [])
        assert not store.ready

    def test_empty_oi_not_ready(self):
        store = _make_store([101], [201], [])
        assert not store.ready

    def test_loaded_store_ready(self):
        oi_rows = [(_T_MINUS_1, 101, 100), (_T_MINUS_1, 201, 100)]
        store = _make_store([101], [201], oi_rows)
        assert store.ready

    def test_unloaded_store_not_ready(self):
        store = OIStore.__new__(OIStore)
        store._loaded = False
        store._def_map = None
        store._oi = None
        store._options_root = None
        store._root_asset = "EW3"
        assert not store.ready
