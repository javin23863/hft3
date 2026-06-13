from __future__ import annotations

from pathlib import Path

from hft3.validation.options_defect_ledger import load_options_defect_ledger


def _write_options_spec(root: Path, rows: str) -> None:
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(
        "# OPTIONS_LANE.md\n\n"
        "| ID | Component | Description | Status |\n"
        "|----|-----------|-------------|--------|\n"
        f"{rows}\n",
        encoding="utf-8",
    )


def test_options_defect_ledger_parses_open_items(tmp_path: Path) -> None:
    _write_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | placeholder | **OPEN** - blocks shadow/live arm. |\n"
        "| o-b | `expiry_calendar` | verified | **FIXED** |",
    )

    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "blocked"
    assert ledger.empty is False
    assert ledger.open_count == 1
    assert ledger.open_ids == ("o-a",)


def test_options_defect_ledger_empty_when_no_open_rows(tmp_path: Path) -> None:
    _write_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | calibrated | **FIXED** |\n"
        "| o-b | `expiry_calendar` | verified | **WAIVED** - documented compliance waiver. |",
    )

    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "empty"
    assert ledger.empty is True
    assert ledger.open_count == 0


def test_options_defect_ledger_uses_final_status_cell(tmp_path: Path) -> None:
    _write_options_spec(
        tmp_path,
        "| o-i | `latency` | R|API+ text contains a pipe | **FIXED** |",
    )

    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "empty"
    assert ledger.empty is True
    assert ledger.open_count == 0


def test_options_defect_ledger_unknown_status_fails_closed(tmp_path: Path) -> None:
    _write_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | placeholder | **PENDING_REVIEW** |\n"
        "| o-b | `expiry_calendar` | verified | **FIXED** |",
    )

    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "unknown_status"
    assert ledger.empty is False
    assert ledger.open_count == 1
    assert ledger.open_ids == ("o-a",)
    assert ledger.items[0]["unknown_status"] is True


def test_options_defect_ledger_malformed_row_fails_closed(tmp_path: Path) -> None:
    _write_options_spec(
        tmp_path,
        "| o-a | `vol_clock` | missing status column |\n"
        "| o-b | `expiry_calendar` | verified | **FIXED** |",
    )

    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "malformed"
    assert ledger.empty is False
    assert ledger.open_count == 1
    assert ledger.open_ids == ("o-a",)
    assert ledger.items[0]["is_malformed"] is True


def test_options_defect_ledger_missing_fails_closed(tmp_path: Path) -> None:
    ledger = load_options_defect_ledger(tmp_path)

    assert ledger.status == "missing"
    assert ledger.empty is False
    assert ledger.open_count == 1
    assert ledger.open_ids == ("OPTIONS_LEDGER_MISSING",)
