"""Tests for research_pipeline.data_quality (Phase 0 NPZ / OHLCV checks)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from research_pipeline.data_quality import (
    NoOHLCVDataError,
    abort_on_failed_units_for_scope,
    check_npz_ohlcv,
    classify_evaluation_error,
    is_no_ohlcv_error,
    skipped_unit_id_set,
    unit_matches_skip,
)


def _event_dtype():
    return np.dtype(
        [
            ("ev", "u8"),
            ("local_ts", "i8"),
            ("px", "f8"),
            ("qty", "f8"),
            ("order_id", "u8"),
        ]
    )


def _write_npz(path: Path, count: int) -> None:
    dtype = _event_dtype()
    data = np.zeros(count, dtype=dtype)
    data["local_ts"] = np.arange(count, dtype=np.int64)
    data["px"] = 100.0
    data["qty"] = 1.0
    data["ev"] = 1
    np.savez(path, data=data)


def test_check_npz_ohlcv_valid(tmp_path: Path) -> None:
    path = tmp_path / "ok.npz"
    _write_npz(path, 3)
    result = check_npz_ohlcv(path)
    assert result.valid is True
    assert result.event_count == 3


def test_check_npz_ohlcv_insufficient_events(tmp_path: Path) -> None:
    path = tmp_path / "empty.npz"
    _write_npz(path, 1)
    result = check_npz_ohlcv(path)
    assert result.valid is False
    assert result.reason == "insufficient_events"


def test_check_npz_ohlcv_zero_px_false_pass(tmp_path: Path) -> None:
    path = tmp_path / "zero_px.npz"
    dtype = _event_dtype()
    data = np.zeros(3, dtype=dtype)
    data["local_ts"] = np.arange(3, dtype=np.int64)
    data["px"] = 0.0
    data["qty"] = 1.0
    data["ev"] = 1
    np.savez(path, data=data)
    result = check_npz_ohlcv(path)
    assert result.valid is False
    assert result.reason == "ohlcv_derivability_error:non_positive_px"


def test_check_npz_ohlcv_missing_file(tmp_path: Path) -> None:
    result = check_npz_ohlcv(tmp_path / "missing.npz")
    assert result.valid is False
    assert result.reason == "missing_npz"


def test_classify_no_ohlcv_as_data_quality() -> None:
    failure_class, message = classify_evaluation_error(NoOHLCVDataError("bad npz"))
    assert failure_class == "data_quality"
    assert "bad npz" in message


def test_classify_model_error() -> None:
    failure_class, _ = classify_evaluation_error(RuntimeError("gate failed"))
    assert failure_class == "model"


def test_is_no_ohlcv_error_string() -> None:
    assert is_no_ohlcv_error("no_ohlcv_data")
    assert is_no_ohlcv_error("no_ohlcv_data: missing bars")
    assert not is_no_ohlcv_error("vectorbt timeout")
    assert not is_no_ohlcv_error("insufficient_events")


def test_skipped_unit_id_set_inline_and_file(tmp_path: Path) -> None:
    skip_file = tmp_path / "skip.json"
    skip_file.write_text(
        '{"invalid_unit_ids": {"unit_b": "insufficient_events"}}',
        encoding="utf-8",
    )
    ids = skipped_unit_id_set(skip_bad_units_file=skip_file, skipped_unit_ids=["unit_a"])
    assert ids == {"unit_a", "unit_b"}


def test_unit_matches_skip_composite_keys() -> None:
    skip = {"ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT"}
    assert unit_matches_skip(
        "HYP_5|ZN.v.0|EIA_NATGAS_2019_11_28_TIGHT",
        symbol="ZN.v.0",
        event_id="EIA_NATGAS_2019_11_28_TIGHT",
        skip_ids=skip,
    )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("pilot", True),
        ("paid", False),
        ("full_lake", False),
        ("continuous_full_cme", False),
    ],
)
def test_abort_on_failed_units_by_scope(scope: str, expected: bool) -> None:
    cfg = {
        "abort_on_failed_units_by_scope": {
            "pilot": True,
            "paid": False,
            "full_lake": False,
            "continuous_full_cme": False,
        }
    }
    assert abort_on_failed_units_for_scope(scope, cfg) is expected


def test_abort_on_failed_units_scope_overrides_legacy() -> None:
    cfg = {"abort_on_failed_units": True, "abort_on_failed_units_by_scope": {"paid": False}}
    assert abort_on_failed_units_for_scope("paid", cfg) is False
    assert abort_on_failed_units_for_scope("pilot", cfg) is True


def test_evaluation_classifies_no_ohlcv(monkeypatch, tmp_path: Path) -> None:
    from research_pipeline.data_quality import NoOHLCVDataError
    from research_pipeline.evaluation import evaluate_model
    from research_pipeline.types import CandidateModel

    class _FakeEngine:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            raise NoOHLCVDataError("no_ohlcv_data")

    monkeypatch.setattr("workbench.src.run.engine.WorkbenchEngine", _FakeEngine)
    monkeypatch.setattr(
        "features_engine.src.model_registry.resolve_model_id",
        lambda model_id: model_id,
    )

    cand = CandidateModel(
        candidate_id="c1",
        model_id="HYP_5",
        strategy_params={},
        thesis="t",
    )
    result = evaluate_model(cand, "CPI_2024_09_11_TIGHT", tmp_path)
    assert result.failure_class == "data_quality"
    assert "no_ohlcv" in (result.error or "")
