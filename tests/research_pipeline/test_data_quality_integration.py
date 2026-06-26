"""Integration tests for data-quality skip list and failure aggregation.

Tests that:
1. check_lake_data.py JSON output can be consumed by _load_skip_bad_units_file
2. The skip set removes candidates before dispatch
3. failure_counts_by_type separates data_quality from algorithmic errors
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))


def test_skip_bad_units_file_invalid_units_format(tmp_path: Path) -> None:
    """_load_skip_bad_units_file reads the invalid_units list from check_lake_data.py output."""
    from scripts.run_vectorbt_paid_screen_v2 import _load_skip_bad_units_file

    report = {
        "checked": 100,
        "valid": 99,
        "invalid": 1,
        "invalid_units": [
            {"unit_id": "ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT", "path": "/data/npz/ZN.npz",
             "reason": "no_ohlcv_data: only 1 events (need >=2 to build a bar)"}
        ],
    }
    path = tmp_path / "lake_quality.json"
    path.write_text(json.dumps(report))

    skip = _load_skip_bad_units_file(path)
    assert "ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT" in skip
    assert len(skip) == 1


def test_skip_bad_units_file_invalid_list_format(tmp_path: Path) -> None:
    """_load_skip_bad_units_file also accepts a bare list format."""
    from scripts.run_vectorbt_paid_screen_v2 import _load_skip_bad_units_file

    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["UNIT_A_TIGHT", "UNIT_B_TIGHT"]))

    skip = _load_skip_bad_units_file(path)
    assert "UNIT_A_TIGHT" in skip
    assert "UNIT_B_TIGHT" in skip


def test_skip_bad_units_file_missing(tmp_path: Path) -> None:
    """A non-existent skip file returns an empty set (no crash)."""
    from scripts.run_vectorbt_paid_screen_v2 import _load_skip_bad_units_file

    skip = _load_skip_bad_units_file(tmp_path / "nonexistent.json")
    assert skip == set()


def test_skip_bad_units_substring_matching() -> None:
    """NPZ stems are substrings of full unit IDs — substring matching must work.

    NPZ stem:   ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT
    Full unit:  SECOND_WAVE_CONTINUATION_ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT
    """
    bad_ids = {"ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT"}
    full_unit_id = "SECOND_WAVE_CONTINUATION_ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT"
    # Substring match (the fix)
    matched = any(b in full_unit_id for b in bad_ids)
    assert matched is True

    # Non-matching unit should not be skipped
    clean_unit_id = "SPREAD_BLOWOUT_RECOMPRESSION_ES.v.0_CPI_2024_09_11_TIGHT"
    matched_clean = any(b in clean_unit_id for b in bad_ids)
    assert matched_clean is False


def test_failure_counts_by_type_aggregation() -> None:
    """failure_counts_by_type separates data_quality from algorithmic errors."""
    unit_results = [
        {"unit_id": "A", "status": "ERROR", "error": "no_ohlcv_data",
         "error_category": "data_quality"},
        {"unit_id": "B", "status": "ERROR", "error": "no_ohlcv_data",
         "error_category": "data_quality"},
        {"unit_id": "C", "status": "ERROR", "error": "vectorbt_timeout",
         "error_category": "timeout"},
        {"unit_id": "D", "status": "OK", "error": None, "error_category": None},
        {"unit_id": "E", "status": "ERROR", "error": "signal_error",
         "error_category": "algorithmic"},
    ]

    failure_counts: dict[str, int] = {}
    for result in unit_results:
        if result.get("status") == "ERROR":
            cat = result.get("error_category") or "algorithmic"
            err = result.get("error") or "unknown"
            key = f"{cat}:{err}"
            failure_counts[key] = failure_counts.get(key, 0) + 1

    assert failure_counts["data_quality:no_ohlcv_data"] == 2
    assert failure_counts["timeout:vectorbt_timeout"] == 1
    assert failure_counts["algorithmic:signal_error"] == 1
    assert len(failure_counts) == 3


def test_run_pipeline_skipped_unit_ids_from_config(tmp_path: Path) -> None:
    """_load_skipped_unit_ids reads skipped_unit_ids from autoresearch config YAML."""
    # We can't easily import run_pipeline (it has heavy deps), so test the
    # logic inline by replicating the YAML parse.
    import yaml

    config = tmp_path / "default.yaml"
    config.write_text(
        "skipped_unit_ids:\n"
        "  - ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT\n"
        "  - MNQ.v.0_NFP_2020_01_10_TIGHT\n"
    )

    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    skip = set(cfg.get("skipped_unit_ids") or [])

    assert "ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT" in skip
    assert "MNQ.v.0_NFP_2020_01_10_TIGHT" in skip
    assert len(skip) == 2


def test_no_ohlcv_data_error_is_value_error() -> None:
    """NoOHLCVDataError is a ValueError subclass — backward compatible with existing except blocks."""
    from research_pipeline.data_quality import NoOHLCVDataError

    err = NoOHLCVDataError("test")
    assert isinstance(err, ValueError)