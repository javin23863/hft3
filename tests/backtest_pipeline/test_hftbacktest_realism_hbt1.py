from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt1
from backtest_pipeline.src.hftbacktest_realism import (
    validate_hftbacktest_data_path,
    validate_hftbacktest_event_array,
    write_hftbacktest_realism_artifacts,
)


def _load_hftbacktest_contract() -> tuple[np.dtype, dict[str, int]]:
    try:
        from hftbacktest.types import (  # type: ignore
            ADD_ORDER_EVENT,
            CANCEL_ORDER_EVENT,
            DEPTH_EVENT,
            EXCH_EVENT,
            FILL_EVENT,
            LOCAL_EVENT,
            MODIFY_ORDER_EVENT,
            event_dtype,
        )
    except Exception:
        event_dtype = np.dtype(
            [
                ("ev", np.uint32),
                ("exch_ts", np.int64),
                ("local_ts", np.int64),
                ("px", np.float64),
                ("qty", np.float64),
                ("order_id", np.int64),
                ("ival", np.int64),
                ("fval", np.float64),
            ]
        )
        return event_dtype, {
            "EXCH_EVENT": 1 << 8,
            "LOCAL_EVENT": 1 << 9,
            "ADD_ORDER_EVENT": 10,
            "DEPTH_EVENT": 1,
            "TRADE_EVENT": 2,
            "CANCEL_ORDER_EVENT": 11,
            "MODIFY_ORDER_EVENT": 12,
            "FILL_EVENT": 13,
        }
    return event_dtype, {
        "EXCH_EVENT": int(EXCH_EVENT),
        "LOCAL_EVENT": int(LOCAL_EVENT),
        "ADD_ORDER_EVENT": int(ADD_ORDER_EVENT),
        "DEPTH_EVENT": int(DEPTH_EVENT),
        "TRADE_EVENT": 2,
        "CANCEL_ORDER_EVENT": int(CANCEL_ORDER_EVENT),
        "MODIFY_ORDER_EVENT": int(MODIFY_ORDER_EVENT),
        "FILL_EVENT": int(FILL_EVENT),
    }


@pytest.fixture()
def hbt_contract(monkeypatch: pytest.MonkeyPatch) -> tuple[np.dtype, dict[str, int]]:
    event_dtype, constants = _load_hftbacktest_contract()

    monkeypatch.setattr(hbt1, "_expected_event_dtype", lambda: event_dtype)
    monkeypatch.setattr(hbt1, "_event_constants", lambda: constants)

    try:
        from hftbacktest.data import validate_event_order as _validate_event_order  # type: ignore
    except Exception:
        fake_data = ModuleType("hftbacktest.data")
        fake_data.validate_event_order = lambda events: None  # type: ignore[attr-defined]
        fake_pkg = ModuleType("hftbacktest")
        fake_pkg.__path__ = []  # type: ignore[attr-defined]
        fake_pkg.data = fake_data  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "hftbacktest", fake_pkg)
        monkeypatch.setitem(sys.modules, "hftbacktest.data", fake_data)

    return event_dtype, constants


def _event_row(
    constants: dict[str, int],
    *,
    event_bits: int,
    exch_ts: int,
    local_ts: int,
    order_id: int = 1,
    px: float = 5000.0,
    qty: float = 1.0,
) -> dict[str, object]:
    return {
        "ev": int(event_bits),
        "exch_ts": int(exch_ts),
        "local_ts": int(local_ts),
        "px": float(px),
        "qty": float(qty),
        "order_id": int(order_id),
        "ival": 0,
        "fval": 0.0,
    }


def _make_events(event_dtype: np.dtype, rows: list[dict[str, object]]) -> np.ndarray:
    events = np.zeros(len(rows), dtype=event_dtype)
    for index, row in enumerate(rows):
        for field, value in row.items():
            events[index][field] = value
    return events


def test_validate_hftbacktest_event_array_uses_installed_official_validator() -> None:
    from hftbacktest import types as real_types  # type: ignore
    from hftbacktest.data import validate_event_order as official_validate_event_order  # type: ignore

    constants = {
        "EXCH_EVENT": int(real_types.EXCH_EVENT),
        "LOCAL_EVENT": int(real_types.LOCAL_EVENT),
        "ADD_ORDER_EVENT": int(real_types.ADD_ORDER_EVENT),
    }
    events = _make_events(
        real_types.event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_100,
                order_id=1001,
            ),
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_600,
                order_id=1002,
            ),
        ],
    )

    official_validate_event_order(events)
    result = validate_hftbacktest_event_array(
        events,
        data_path="real_official_validator.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "pass"
    assert result["official_validate_event_order_status"] == "pass"
    assert result["dtype_exact_match"] is True


def test_validate_hftbacktest_event_array_accepts_valid_l3_add_fixture(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
                order_id=1001,
            ),
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_500,
                order_id=1002,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="valid_l3.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "pass"
    assert result["l2_l3_classification"] == "l3_mbo"
    assert result["dtype_exact_match"] is True
    assert result["orphan_l3_event_count"] == 0
    assert result["fail_closed_reasons"] == []


def test_validate_hftbacktest_event_array_accepts_valid_l2_depth_fixture(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
                order_id=0,
            ),
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_500,
                order_id=0,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="valid_l2.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "pass"
    assert result["l2_l3_classification"] == "l2_mbp"
    assert result["dtype_exact_match"] is True
    assert result["orphan_l3_event_count"] == 0
    assert result["fail_closed_reasons"] == []


def test_validate_hftbacktest_event_array_marks_official_validator_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    monkeypatch.setitem(sys.modules, "hftbacktest.data", None)
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_100,
                order_id=1001,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="validator_unavailable.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["official_validate_event_order_status"].startswith("unavailable:")
    assert "HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE" in result["fail_closed_reasons"]
    assert "HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED" not in result["fail_closed_reasons"]


def test_validator_unavailable_maps_to_top_level_fail_status() -> None:
    status = hbt1._replay_status_from_fail_reasons(
        ["HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE"],
        {"data_validation_status": "fail"},
    )

    assert status == "fail"


def test_validate_hftbacktest_event_array_rejects_unproven_timestamp_units(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_100,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(events, data_path="units_missing.npz")

    assert result["data_validation_status"] == "fail"
    assert result["timestamp_units"] == "unproven"
    assert "TIMESTAMP_UNITS_UNPROVEN" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_mixed_l2_l3(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
            ),
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_500,
                order_id=42,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="mixed.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["l2_l3_classification"] == "mixed_rejected"
    assert "L2_L3_MISMATCH" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_trade_plus_l3_mixed(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["TRADE_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_100,
                order_id=0,
            ),
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_600,
                order_id=42,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="trade_plus_l3.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["l2_l3_classification"] == "mixed_rejected"
    assert "L2_L3_MISMATCH" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_empty_events(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, _constants = hbt_contract
    events = np.zeros(0, dtype=event_dtype)

    result = validate_hftbacktest_event_array(
        events,
        data_path="empty.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["l2_l3_classification"] == "empty_rejected"
    assert "EVENT_ARRAY_EMPTY" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_unknown_event_type(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=99 | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_100,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="unknown_event.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["l2_l3_classification"] == "unknown_rejected"
    assert "EVENT_TYPE_UNKNOWN" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_counts_orphan_l3_events(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
                order_id=10,
            ),
            _event_row(
                constants,
                event_bits=constants["CANCEL_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_500,
                order_id=20,
            ),
            _event_row(
                constants,
                event_bits=constants["MODIFY_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_001_000,
                local_ts=1_000_001_000,
                order_id=30,
            ),
            _event_row(
                constants,
                event_bits=constants["FILL_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_001_500,
                local_ts=1_000_001_500,
                order_id=40,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="orphans.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["orphan_l3_event_count"] == 3
    assert result["orphan_l3_order_id_sample"] == [20, 30, 40]
    assert "ORPHAN_L3_EVENTS_UNACCOUNTED" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_bad_dtype(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    bad_dtype = np.dtype(
        [
            ("ev", event_dtype["ev"].type),
            ("exch_ts", event_dtype["exch_ts"].type),
            ("local_ts", event_dtype["local_ts"].type),
        ]
    )
    events = np.zeros(
        1,
        dtype=bad_dtype,
    )
    events[0]["ev"] = constants["ADD_ORDER_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"]
    events[0]["exch_ts"] = 1_000_000_000
    events[0]["local_ts"] = 1_000_000_000

    result = validate_hftbacktest_event_array(
        events,
        data_path="bad_dtype.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["fail_closed_reasons"] == ["EVENT_DTYPE_INVALID"]
    assert set(result["missing_dtype_fields"]) == {"px", "qty", "order_id", "ival", "fval"}


def test_validate_hftbacktest_data_path_rejects_missing_data_array(
    tmp_path: Path,
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    npz_path = tmp_path / "missing_data.npz"
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
            ),
        ],
    )
    np.savez_compressed(npz_path, not_data=events)

    result = validate_hftbacktest_data_path(npz_path)

    assert result["data_validation_status"] == "fail"
    assert result["fail_closed_reasons"] == ["DATA_NPZ_MISSING_DATA_ARRAY"]
    assert result["data_path"] == str(npz_path)


def test_validate_hftbacktest_event_array_rejects_non_monotonic_exchange_timestamps(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    rows = [
        _event_row(
            constants,
            event_bits=constants["ADD_ORDER_EVENT"] | constants["EXCH_EVENT"],
            exch_ts=1_000_000_000,
            local_ts=1_000_000_000,
            order_id=1,
        ),
        _event_row(
            constants,
            event_bits=constants["ADD_ORDER_EVENT"] | constants["EXCH_EVENT"],
            exch_ts=900_000_000,
            local_ts=1_000_000_500,
            order_id=2,
        ),
    ]

    events = _make_events(event_dtype, rows)
    result = validate_hftbacktest_event_array(
        events,
        data_path="exchange_ts.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert "EXCHANGE_ORDER_INVALID" in result["fail_closed_reasons"]
    assert "HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_non_monotonic_local_timestamps(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    rows = [
        _event_row(
            constants,
            event_bits=constants["ADD_ORDER_EVENT"] | constants["LOCAL_EVENT"],
            exch_ts=1_000_000_000,
            local_ts=1_000_000_200,
            order_id=1,
        ),
        _event_row(
            constants,
            event_bits=constants["ADD_ORDER_EVENT"] | constants["LOCAL_EVENT"],
            exch_ts=1_000_000_100,
            local_ts=1_000_000_100,
            order_id=2,
        ),
    ]

    events = _make_events(event_dtype, rows)
    result = validate_hftbacktest_event_array(
        events,
        data_path="local_ts.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert "LOCAL_ORDER_INVALID" in result["fail_closed_reasons"]
    assert "HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED" in result["fail_closed_reasons"]


def test_validate_hftbacktest_event_array_rejects_negative_feed_latency(
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=999_999_900,
                order_id=101,
            ),
            _event_row(
                constants,
                event_bits=constants["ADD_ORDER_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_400,
                order_id=102,
            ),
        ],
    )

    result = validate_hftbacktest_event_array(
        events,
        data_path="negative_latency.npz",
        timestamp_units="nanoseconds",
    )

    assert result["data_validation_status"] == "fail"
    assert result["feed_latency_status"] == "fail"
    assert "NEGATIVE_FEED_LATENCY_UNCORRECTED" in result["fail_closed_reasons"]


def test_write_hftbacktest_realism_artifacts_writes_data_validation_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hbt_contract: tuple[np.dtype, dict[str, int]],
) -> None:
    event_dtype, constants = hbt_contract
    monkeypatch.setattr(hbt1, "_repo_commit", lambda _root: "deadbeef")
    monkeypatch.setattr(hbt1, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt1,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )

    data_path = tmp_path / "valid_data.npz"
    events = _make_events(
        event_dtype,
        [
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_000,
                local_ts=1_000_000_000,
            ),
            _event_row(
                constants,
                event_bits=constants["DEPTH_EVENT"]
                | constants["EXCH_EVENT"]
                | constants["LOCAL_EVENT"],
                exch_ts=1_000_000_500,
                local_ts=1_000_000_500,
            ),
        ],
    )
    np.savez_compressed(data_path, data=events, timestamp_units="nanoseconds")

    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt1_test"
    payload = write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        data_npz_path=data_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"],
        run_id="hbt1_test",
    )

    data_validation_path = out_dir / "data_validation.json"
    assert data_validation_path.is_file()
    data_validation = json.loads(data_validation_path.read_text(encoding="utf-8"))
    assert data_validation["data_validation_status"] == "pass"
    assert data_validation["l2_l3_classification"] == "l2_mbp"
    assert payload["replay_summary"]["data_validation_status"] == "pass"
