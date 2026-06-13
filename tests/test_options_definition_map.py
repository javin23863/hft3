"""Tests for packages/options_lane/studies/definition_map.py.

All tests use *synthetic* fixtures — no real lake files required.
Fake InstrumentDefMsg objects are built as simple namespaces so the test suite
runs offline and without a Databento subscription.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from options_lane.studies.definition_map import (
    OptionMeta,
    instruments_expiring_on,
    load_definition_map,
)

# ---------------------------------------------------------------------------
# Constants mirrored from the module under test
# ---------------------------------------------------------------------------
_INT64_MAX = 9_223_372_036_854_775_807
_SCALE = 1_000_000_000
_UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Fake record helpers
# ---------------------------------------------------------------------------

def _ns(d: datetime.date) -> int:
    """Convert a UTC date to nanoseconds (midnight UTC)."""
    dt = datetime.datetime(d.year, d.month, d.day, tzinfo=_UTC)
    return int(dt.timestamp() * 1e9)


class _FakeIC:
    """Minimal stand-in for databento_dbn.InstrumentClass enum values."""
    CALL = "C"
    PUT = "P"
    OPTION_SPREAD = "T"
    FUTURE = "F"


def _def_rec(
    instrument_id: int,
    instrument_class: str,
    strike_price: int,
    expiration_date: datetime.date,
    underlying: str = "ESM3",
    asset: str = "EW3",
    raw_symbol: str = "EW3K3 X0000",
) -> SimpleNamespace:
    """Build a fake InstrumentDefMsg-like namespace."""
    return SimpleNamespace(
        instrument_id=instrument_id,
        instrument_class=instrument_class,
        strike_price=strike_price,
        expiration=_ns(expiration_date),
        underlying=underlying,
        asset=asset,
        raw_symbol=raw_symbol,
        rtype="instrument-def",
    )


# ---------------------------------------------------------------------------
# Fake DBNStore
# ---------------------------------------------------------------------------

class _FakeStore:
    """Minimal DBNStore stand-in: iterates over a list of pre-built records."""

    def __init__(self, records: list) -> None:
        self._records = records

    def __iter__(self) -> Iterator:
        return iter(self._records)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXP_A = datetime.date(2023, 5, 19)
EXP_B = datetime.date(2023, 6, 16)

CALL_A = _def_rec(
    instrument_id=1001,
    instrument_class=_FakeIC.CALL,
    strike_price=4750 * _SCALE,
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="EW3",
    raw_symbol="EW3K3 C4750",
)

PUT_A = _def_rec(
    instrument_id=1002,
    instrument_class=_FakeIC.PUT,
    strike_price=4500 * _SCALE,
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="EW3",
    raw_symbol="EW3K3 P4500",
)

SPREAD_A = _def_rec(
    instrument_id=1003,
    instrument_class=_FakeIC.OPTION_SPREAD,
    strike_price=4600 * _SCALE,
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="EW3",
    raw_symbol="EW3K3 SPRD",
)

FUTURE_A = _def_rec(
    instrument_id=1004,
    instrument_class=_FakeIC.FUTURE,
    strike_price=_INT64_MAX,
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="ES",
    raw_symbol="ESM3",
)

SENTINEL_STRIKE = _def_rec(
    instrument_id=1005,
    instrument_class=_FakeIC.CALL,
    strike_price=_INT64_MAX,      # sentinel — should be excluded
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="EW3",
    raw_symbol="EW3K3 C????",
)

CALL_B = _def_rec(
    instrument_id=2001,
    instrument_class=_FakeIC.CALL,
    strike_price=5000 * _SCALE,
    expiration_date=EXP_B,
    underlying="ESU3",
    asset="EW3",
    raw_symbol="EW3M3 C5000",
)

# A second version of CALL_A with updated raw_symbol (simulates a day-delta update)
CALL_A_V2 = _def_rec(
    instrument_id=1001,                # same id as CALL_A
    instrument_class=_FakeIC.CALL,
    strike_price=4750 * _SCALE,
    expiration_date=EXP_A,
    underlying="ESM3",
    asset="EW3",
    raw_symbol="EW3K3 C4750-UPDATED",  # changed field to verify latest-wins
)


# ---------------------------------------------------------------------------
# Patching helper
# ---------------------------------------------------------------------------

def _patch_dbn(records_per_path: dict[str, list]):
    """
    Context manager: patch ``databento.DBNStore.from_file`` so that each call
    with a given path string returns a ``_FakeStore`` with the corresponding
    records.  Also patches ``databento_dbn.InstrumentClass`` to ``_FakeIC``.
    """
    def fake_from_file(path: str):
        path_str = str(path)
        # Match by filename stem to keep paths portable
        for key, recs in records_per_path.items():
            if key in path_str:
                return _FakeStore(recs)
        return _FakeStore([])

    mock_db = MagicMock()
    mock_db.DBNStore.from_file.side_effect = fake_from_file

    mock_dbn = MagicMock()
    mock_dbn.InstrumentClass = _FakeIC

    return (
        patch("options_lane.studies.definition_map.databento", mock_db, create=True),
        patch("options_lane.studies.definition_map.databento_dbn", mock_dbn, create=True),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCallPutKept:
    """CALL and PUT records are included; others are dropped."""

    def test_call_and_put_are_in_map(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A, PUT_A, SPREAD_A, FUTURE_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])

        assert 1001 in result, "CALL should be in map"
        assert 1002 in result, "PUT should be in map"

    def test_spread_excluded(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [SPREAD_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert 1003 not in result, "OPTION_SPREAD should be excluded"

    def test_future_excluded(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [FUTURE_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert 1004 not in result, "FUTURE should be excluded"

    def test_sentinel_strike_excluded(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [SENTINEL_STRIKE]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert 1005 not in result, "Sentinel strike INT64_MAX should be excluded"


class TestFieldConversion:
    """Strike and expiration are decoded correctly."""

    def test_strike_conversion(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        meta = result[1001]
        assert abs(meta.strike - 4750.0) < 1e-6, f"Expected 4750.0 but got {meta.strike}"

    def test_expiration_date(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert result[1001].expiration == EXP_A

    def test_right_call(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert result[1001].right == "C"

    def test_right_put(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [PUT_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        assert result[1002].right == "P"

    def test_metadata_fields(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A]})
        with ctx1, ctx2:
            result = load_definition_map([p])
        meta = result[1001]
        assert meta.underlying == "ESM3"
        assert meta.root_asset == "EW3"
        assert meta.raw_symbol == "EW3K3 C4750"


class TestCumulativeLatestWins:
    """When two files define the same instrument_id, the later file wins."""

    def test_latest_definition_wins(self, tmp_path):
        p1 = tmp_path / "day1.dbn.zst"
        p2 = tmp_path / "day2.dbn.zst"
        p1.touch()
        p2.touch()
        ctx1, ctx2 = _patch_dbn(
            {str(p1): [CALL_A], str(p2): [CALL_A_V2]}
        )
        with ctx1, ctx2:
            result = load_definition_map([p1, p2])
        # p2 is processed after p1, so its version should win
        assert result[1001].raw_symbol == "EW3K3 C4750-UPDATED"

    def test_earlier_file_does_not_overwrite_later(self, tmp_path):
        p1 = tmp_path / "day1.dbn.zst"
        p2 = tmp_path / "day2.dbn.zst"
        p1.touch()
        p2.touch()
        # Reverse: p1 processed first => p2 version persists
        ctx1, ctx2 = _patch_dbn(
            {str(p1): [CALL_A_V2], str(p2): [CALL_A]}
        )
        with ctx1, ctx2:
            result = load_definition_map([p1, p2])
        # p2 is last, so plain CALL_A version should win
        assert result[1001].raw_symbol == "EW3K3 C4750"

    def test_new_instruments_from_second_file_added(self, tmp_path):
        p1 = tmp_path / "day1.dbn.zst"
        p2 = tmp_path / "day2.dbn.zst"
        p1.touch()
        p2.touch()
        ctx1, ctx2 = _patch_dbn(
            {str(p1): [CALL_A], str(p2): [CALL_B]}
        )
        with ctx1, ctx2:
            result = load_definition_map([p1, p2])
        assert 1001 in result
        assert 2001 in result


class TestInstrumentsExpiringOn:
    """instruments_expiring_on filters correctly by date."""

    def _build_map(self, tmp_path):
        p = tmp_path / "defs.dbn.zst"
        p.touch()
        ctx1, ctx2 = _patch_dbn({str(p): [CALL_A, PUT_A, CALL_B]})
        with ctx1, ctx2:
            return load_definition_map([p])

    def test_returns_correct_ids_for_date(self, tmp_path):
        def_map = self._build_map(tmp_path)
        result = instruments_expiring_on(def_map, EXP_A)
        assert set(result) == {1001, 1002}

    def test_other_date_excluded(self, tmp_path):
        def_map = self._build_map(tmp_path)
        result = instruments_expiring_on(def_map, EXP_B)
        assert set(result) == {2001}

    def test_no_match_returns_empty(self, tmp_path):
        def_map = self._build_map(tmp_path)
        result = instruments_expiring_on(def_map, datetime.date(2099, 1, 1))
        assert result == []


class TestDirectoryInput:
    """Passing a directory discovers and processes *.dbn.zst files."""

    def test_directory_rglob(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        p1 = tmp_path / "a.dbn.zst"
        p2 = sub / "b.dbn.zst"
        p1.touch()
        p2.touch()
        ctx1, ctx2 = _patch_dbn(
            {str(p1): [CALL_A], str(p2): [CALL_B]}
        )
        with ctx1, ctx2:
            result = load_definition_map(tmp_path)
        assert 1001 in result
        assert 2001 in result


class TestOptionMetaDataclass:
    """OptionMeta is frozen and has the expected fields."""

    def test_frozen_immutable(self):
        meta = OptionMeta(
            expiration=EXP_A,
            strike=4750.0,
            right="C",
            underlying="ESM3",
            root_asset="EW3",
            raw_symbol="EW3K3 C4750",
        )
        with pytest.raises((AttributeError, TypeError)):
            meta.strike = 9999.0  # type: ignore[misc]

    def test_none_strike_allowed(self):
        meta = OptionMeta(
            expiration=EXP_A,
            strike=None,
            right="P",
            underlying="ESM3",
            root_asset="EW3",
            raw_symbol="EW3K3 P????",
        )
        assert meta.strike is None
