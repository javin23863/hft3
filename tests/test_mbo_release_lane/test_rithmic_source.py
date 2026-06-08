"""Tests for the Rithmic MBO fill source + source-priority resolver.

All tests run on the workstation: the topology guard refuses on Windows
without ever opening a socket, the schema/parser tests are pure-function,
and the resolver tests use a temp repo tree.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from mbo_release_lane.constants import SOURCE_VENDOR_RITHMIC
from mbo_release_lane.rithmic_source import (
    _infer_data_label,
    _normalize_action,
    _normalize_side,
    _coerce_timestamp_ns,
    _tick_to_event,
    fetch_event_window,
    RithmicFetchResult,
)
from mbo_release_lane.rithmic_topology_guard import (
    RithmicTopologyError,
    assert_rithmic_topology_ok,
    is_windows,
)
from mbo_release_lane.source_priority import (
    SourceSlotStatus,
    attempt_rithmic_fill,
    resolve_source,
)


# ---------------------------------------------------------------------------
# hard labeling rule
# ---------------------------------------------------------------------------


def test_infer_data_label_true_mbo():
    rec = {
        "order_id": 1,
        "action": "A",
        "side": "B",
        "flags": 0,
        "bid_price": 5000.0,
        "ask_price": 5000.25,
    }
    assert _infer_data_label(rec) == "mbo"


def test_infer_data_label_mbo_no_depth_still_mbo():
    """MBO without depth context: heuristic still labels mbo (order-id proof)."""
    rec = {"order_id": 1, "action": "C", "side": "A", "flags": 0}
    assert _infer_data_label(rec) == "mbo"


def test_infer_data_label_ticks():
    rec = {"timestamp": 1_000_000, "price": 5000.0, "size": 1}
    assert _infer_data_label(rec) == "ticks"


def test_infer_data_label_depth_mbp_only():
    rec = {"bid_price": 5000.0, "ask_price": 5000.25, "bid_size": 10, "ask_size": 5}
    assert _infer_data_label(rec) == "depth/mbp"


def test_infer_data_label_unknown():
    assert _infer_data_label({}) == "unknown"


# ---------------------------------------------------------------------------
# record normalization
# ---------------------------------------------------------------------------


def test_normalize_action():
    assert _normalize_action("a") == "add"
    assert _normalize_action("CANCEL") == "cancel"
    assert _normalize_action("T") == "trade"
    assert _normalize_action("F") == "fill"
    assert _normalize_action("r") == "delete"
    assert _normalize_action(None) == "add"
    assert _normalize_action("unknown") == "unknown"


def test_normalize_side():
    assert _normalize_side("B") == "B"
    assert _normalize_side("ASK") == "A"
    assert _normalize_side("S") == "A"
    assert _normalize_side("BUY") == "B"
    assert _normalize_side("") == "N"
    assert _normalize_side(None) == "N"


def test_coerce_timestamp_ns_from_datetime():
    dt = datetime(2025, 5, 15, 15, 30, 0, tzinfo=timezone.utc)
    ns = _coerce_timestamp_ns(dt)
    assert ns == int(dt.timestamp() * 1_000_000_000)


def test_coerce_timestamp_ns_from_int_seconds():
    ns = _coerce_timestamp_ns(1_700_000_000)  # ~2023 in seconds
    assert ns == 1_700_000_000 * 1_000_000_000


def test_coerce_timestamp_ns_from_int_milliseconds():
    ns = _coerce_timestamp_ns(1_700_000_000_000)  # 2023 in ms
    assert ns == 1_700_000_000_000 * 1_000_000


def test_coerce_timestamp_ns_from_int_nanoseconds():
    ns = _coerce_timestamp_ns(1_700_000_000_000_000_000)
    assert ns == 1_700_000_000_000_000_000


def test_tick_to_event_produces_mbo_release_lane_schema():
    tick = {
        "timestamp": 1_700_000_000_000_000_000,
        "price": 5000.25,
        "size": 1,
        "order_id": "42",
        "action": "A",
        "side": "B",
        "flags": 0,
        "trade_id": "",
        "match_id": "",
    }
    ev = _tick_to_event(tick, release_id="CPI_2024_09_11_TIGHT", symbol="ESM5", sequence=1)
    assert ev["release_id"] == "CPI_2024_09_11_TIGHT"
    assert ev["symbol"] == "ESM5"
    assert ev["venue"] == "GLBX"
    assert ev["order_id"] == 42
    assert ev["action"] == "add"
    assert ev["side"] == "B"
    assert ev["price"] == "5000.25"
    assert ev["size"] == "1"
    assert ev["source_vendor"] == SOURCE_VENDOR_RITHMIC
    assert ev["dataset_id"] == "RITHMIC_HISTORY"
    assert ev["sequence_number"] == 1


# ---------------------------------------------------------------------------
# topology guard
# ---------------------------------------------------------------------------


def test_is_windows_true_on_this_host():
    # This test file runs on Windows; if it isn't, the other Windows
    # tests are vacuous and the test would already have failed.
    assert is_windows() is True


def test_assert_rithmic_topology_ok_raises_on_windows():
    with pytest.raises(RithmicTopologyError, match="CHI404"):
        assert_rithmic_topology_ok()


# ---------------------------------------------------------------------------
# fetch_event_window — credential / topology / import paths
# ---------------------------------------------------------------------------


def test_fetch_event_window_refuses_on_windows():
    """The sync wrapper must refuse to even attempt a fetch on Windows."""
    with pytest.raises(RithmicTopologyError, match="CHI404"):
        fetch_event_window(
            release_id="CPI_2024_09_11_TIGHT",
            symbol="ESM5",
            exchange="CME",
            start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
            end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
        )


def test_fetch_event_window_no_creds_returns_error():
    """When the topology guard is bypassed and no creds are present, return error result."""
    with patch(
        "mbo_release_lane.rithmic_source.assert_rithmic_topology_ok",
        return_value=None,
    ):
        with patch.dict("os.environ", {}, clear=True):
            result = fetch_event_window(
                release_id="CPI_2024_09_11_TIGHT",
                symbol="ESM5",
                exchange="CME",
                start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
                end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
            )
    assert not result.is_valid_mbo
    assert "credentials" in (result.error or "").lower()


def test_fetch_event_window_refuses_non_mbo_schema():
    """When Rithmic returns ticks without order-level fields, refuse to fill MBO slot."""
    fake_ticks = [
        {"timestamp": 1_700_000_000_000_000_000, "price": 5000.0, "size": 1},
        {"timestamp": 1_700_000_000_000_000_000, "price": 5000.0, "size": 2},
    ]

    async def fake_fetch(creds, symbol, exchange, start_utc, end_utc, max_pages):
        return fake_ticks

    with patch(
        "mbo_release_lane.rithmic_source.assert_rithmic_topology_ok",
        return_value=None,
    ):
        with patch.dict(
            "os.environ",
            {
                "RITHMIC_USER": "u",
                "RITHMIC_PASSWORD": "p",
                "RITHMIC_SYSTEM_NAME": "Rithmic Test",
            },
            clear=False,
        ):
            with patch(
                "mbo_release_lane.rithmic_source._fetch_ticks_async",
                side_effect=fake_fetch,
            ):
                result = fetch_event_window(
                    release_id="CPI_2024_09_11_TIGHT",
                    symbol="ESM5",
                    exchange="CME",
                    start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
                    end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
                )
    assert not result.is_valid_mbo
    assert result.data_label == "ticks"
    assert "hard labeling rule" in (result.error or "")
    assert result.events == []


def test_fetch_event_window_accepts_mbo_schema():
    """When Rithmic returns true MBO, normalize and label as mbo."""
    fake_ticks = [
        {
            "timestamp": 1_700_000_000_000_000_000,
            "price": 5000.25,
            "size": 1,
            "order_id": "42",
            "action": "A",
            "side": "B",
            "flags": 0,
        },
        {
            "timestamp": 1_700_000_000_000_000_001,
            "price": 5000.25,
            "size": 1,
            "order_id": "43",
            "action": "A",
            "side": "A",
            "flags": 0,
        },
    ]

    async def fake_fetch(creds, symbol, exchange, start_utc, end_utc, max_pages):
        return fake_ticks

    with patch(
        "mbo_release_lane.rithmic_source.assert_rithmic_topology_ok",
        return_value=None,
    ):
        with patch.dict(
            "os.environ",
            {"RITHMIC_USER": "u", "RITHMIC_PASSWORD": "p"},
            clear=False,
        ):
            with patch(
                "mbo_release_lane.rithmic_source._fetch_ticks_async",
                side_effect=fake_fetch,
            ):
                result = fetch_event_window(
                    release_id="CPI_2024_09_11_TIGHT",
                    symbol="ESM5",
                    exchange="CME",
                    start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
                    end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
                )
    assert result.is_valid_mbo
    assert result.data_label == "mbo"
    assert len(result.events) == 2
    assert all(e["source_vendor"] == SOURCE_VENDOR_RITHMIC for e in result.events)
    # Sequences assigned 1..N
    assert [e["sequence_number"] for e in result.events] == [1, 2]


# ---------------------------------------------------------------------------
# source-priority resolver
# ---------------------------------------------------------------------------


def test_resolve_source_picks_rithmic_first(tmp_path: Path):
    """When slot is empty and we're on Windows (skip Rithmic), resolver returns None or Databento."""
    # On Windows, the resolver should skip Rithmic (BLUEPRINT §4) and
    # fall through to Databento.  This test documents the topology rule.
    chosen = resolve_source(tmp_path, "FAKE_EID", "ESM5", force_source=None)
    # On Windows: Rithmic is skipped → Databento is the answer.
    if is_windows():
        from mbo_release_lane.constants import SOURCE_VENDOR

        assert chosen == SOURCE_VENDOR
    else:
        assert chosen == SOURCE_VENDOR_RITHMIC


def test_resolve_source_force_overrides(tmp_path: Path):
    assert resolve_source(tmp_path, "X", "Y", force_source="databento") == "databento"
    assert resolve_source(tmp_path, "X", "Y", force_source=SOURCE_VENDOR_RITHMIC) == SOURCE_VENDOR_RITHMIC


def test_attempt_rithmic_fill_refuses_on_windows(tmp_path: Path):
    status, manifest, err = attempt_rithmic_fill(
        tmp_path,
        release_id="CPI_2024_09_11_TIGHT",
        symbol="ESM5",
        exchange="CME",
        start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
        end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
    )
    assert status.skipped_reason == "windows"
    assert manifest is None
    assert err and "CHI404" in err


def test_attempt_rithmic_fill_no_creds_returns_error(tmp_path: Path):
    with patch(
        "mbo_release_lane.source_priority.is_windows", return_value=False
    ):
        with patch(
            "mbo_release_lane.rithmic_source.assert_rithmic_topology_ok",
            return_value=None,
        ):
            with patch.dict("os.environ", {}, clear=True):
                status, manifest, err = attempt_rithmic_fill(
                    tmp_path,
                    release_id="CPI_2024_09_11_TIGHT",
                    symbol="ESM5",
                    exchange="CME",
                    start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
                    end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
                    scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
                )
    assert not status.is_filled()
    assert manifest is None
    assert err and "credentials" in err.lower()


def test_source_slot_status_is_filled_when_manifest_valid(tmp_path: Path):
    from mbo_release_lane.storage import (
        build_release_event_path,
        release_slot_dir,
        release_event_path_manifest,
        write_json,
    )

    slot = release_slot_dir(tmp_path, "TEST_EID", "ESM5")
    slot.mkdir(parents=True, exist_ok=True)
    rep = build_release_event_path(
        release_id="TEST_EID",
        release_name="TEST",
        scheduled_release_timestamp="2024-01-01T00:00:00+00:00",
        actual_release_timestamp="2024-01-01T00:00:00+00:00",
        symbol="ESM5",
        venue="GLBX",
        window_start="2024-01-01T00:00:00+00:00",
        window_end="2024-01-01T00:01:00+00:00",
        events_ref="events.jsonl",
        event_count=10,
        first_sequence=1,
        last_sequence=10,
        sequence_gap_count=0,
        source_vendor=SOURCE_VENDOR_RITHMIC,
        dataset_id="RITHMIC_HISTORY",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)

    from mbo_release_lane.source_priority import _slot_status_for_source

    status = _slot_status_for_source(tmp_path, "TEST_EID", "ESM5", SOURCE_VENDOR_RITHMIC)
    assert status.is_filled()
    assert status.on_disk_source == SOURCE_VENDOR_RITHMIC


def test_resolve_source_returns_none_when_other_source_already_filled(tmp_path: Path):
    """If Databento filled the slot, Rithmic must not overwrite it."""
    from mbo_release_lane.storage import (
        build_release_event_path,
        release_slot_dir,
        release_event_path_manifest,
        write_json,
    )

    slot = release_slot_dir(tmp_path, "TEST_EID_2", "ESM5")
    slot.mkdir(parents=True, exist_ok=True)
    rep = build_release_event_path(
        release_id="TEST_EID_2",
        release_name="TEST",
        scheduled_release_timestamp="2024-01-01T00:00:00+00:00",
        actual_release_timestamp="2024-01-01T00:00:00+00:00",
        symbol="ESM5",
        venue="GLBX",
        window_start="2024-01-01T00:00:00+00:00",
        window_end="2024-01-01T00:01:00+00:00",
        events_ref="events.jsonl",
        event_count=10,
        first_sequence=1,
        last_sequence=10,
        sequence_gap_count=0,
        source_vendor="databento",
        dataset_id="GLBX.MDP3",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)

    chosen = resolve_source(tmp_path, "TEST_EID_2", "ESM5", force_source=None)
    assert chosen is None  # already filled by Databento


# ---------------------------------------------------------------------------
# write_release_artifact round-trip
# ---------------------------------------------------------------------------


def test_write_release_artifact_writes_expected_files(tmp_path: Path):
    events = [
        {
            "release_id": "CPI_2024_09_11_TIGHT",
            "symbol": "ESM5",
            "venue": "GLBX",
            "instrument_id": 0,
            "sequence_number": 1,
            "exchange_timestamp": 1_700_000_000_000_000_000,
            "receive_timestamp": 1_700_000_000_000_000_000,
            "event_type": "add",
            "order_id": 1,
            "side": "B",
            "price": "5000.0",
            "size": "1",
            "remaining_size": "1",
            "trade_id": "",
            "match_id": "",
            "action": "add",
            "raw_message": "{}",
            "source_vendor": SOURCE_VENDOR_RITHMIC,
            "dataset_id": "RITHMIC_HISTORY",
            "parser_version": "1.0.0",
        }
    ]
    result = RithmicFetchResult(
        release_id="CPI_2024_09_11_TIGHT",
        symbol="ESM5",
        exchange="CME",
        start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
        end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
        events=events,
        raw_tick_count=1,
        data_label="mbo",
        schema_fields=["order_id", "action", "side", "flags", "price", "size", "timestamp"],
    )

    from mbo_release_lane.rithmic_source import write_release_artifact
    from mbo_release_lane.storage import (
        events_jsonl_path,
        release_event_path_manifest,
        validation_report_path,
        hashes_path,
        release_slot_dir,
    )

    rep = write_release_artifact(
        tmp_path,
        result,
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
    )
    assert rep is not None
    slot = release_slot_dir(tmp_path, "CPI_2024_09_11_TIGHT", "ESM5")
    assert events_jsonl_path(slot).is_file()
    assert release_event_path_manifest(slot).is_file()
    assert validation_report_path(slot).is_file()
    assert hashes_path(slot).is_file()

    manifest = json.loads(release_event_path_manifest(slot).read_text(encoding="utf-8"))
    assert manifest["release_event_path"]["source_vendor"] == SOURCE_VENDOR_RITHMIC
    assert manifest["release_event_path"]["validation_status"] == "valid"
    assert manifest["release_event_path"]["event_count"] == 1


def test_write_release_artifact_refuses_non_mbo(tmp_path: Path):
    """Non-MBO fetch result: write_release_artifact returns None, no files written."""
    result = RithmicFetchResult(
        release_id="CPI_2024_09_11_TIGHT",
        symbol="ESM5",
        exchange="CME",
        start_utc=datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc),
        end_utc=datetime(2024, 9, 11, 12, 30, tzinfo=timezone.utc),
        events=[],
        raw_tick_count=0,
        data_label="ticks",
        error="schema is ticks, not mbo",
    )

    from mbo_release_lane.rithmic_source import write_release_artifact
    from mbo_release_lane.storage import release_slot_dir

    rep = write_release_artifact(
        tmp_path,
        result,
        scheduled_release_timestamp="2024-09-11T12:30:00+00:00",
    )
    assert rep is None
    slot = release_slot_dir(tmp_path, "CPI_2024_09_11_TIGHT", "ESM5")
    assert not slot.exists()  # no directory should be created
