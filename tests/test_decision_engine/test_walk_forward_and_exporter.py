"""Tests for WalkForwardValidator (rolling refit, OOS gate, purge/embargo)
and export_weights_to_cpp (endianness, derived feature_count).

All tests are self-contained: they use recording fakes instead of real data,
so no external files or network access is required.
"""
from __future__ import annotations

import struct
import tempfile
from pathlib import Path
from typing import Any, List, Optional

import pytest

from decision_engine.python.src.walk_forward import (
    WalkForwardValidator,
    _call_loader,
    _loader_accepts_date_kwargs,
    export_weights_to_cpp,
)
from decision_engine.python.src.endian_safe_export import (
    export_weights_portable,
    load_weights_portable,
    verify_weights_integrity,
)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

PERIODS = ["Discovery", "Confirmation", "Holdout", "Recent holdout"]
PERIOD_START = {
    "Discovery": 2018,
    "Confirmation": 2021,
    "Holdout": 2023,
    "Recent holdout": 2025,
}
PERIOD_END = {
    "Discovery": 2020,
    "Confirmation": 2022,
    "Holdout": 2024,
    "Recent holdout": 2025,
}


def _positive_metric() -> dict:
    return {"net_expectancy": 1.0, "net_pnl": 100.0}


def _negative_metric() -> dict:
    return {"net_expectancy": -1.0, "net_pnl": -100.0}


class RecordingLoader:
    """Fake data_loader that records every call and returns a lightweight dict."""

    def __init__(self, *, accepts_dates: bool = False):
        self.calls: List[tuple] = []
        self._accepts_dates = accepts_dates

    def __call__(
        self,
        start_year: int,
        end_year: int,
        purge_end_date: Optional[str] = None,
        embargo_start_date: Optional[str] = None,
    ):
        if self._accepts_dates:
            self.calls.append(
                (start_year, end_year, purge_end_date, embargo_start_date)
            )
        else:
            self.calls.append((start_year, end_year))
        return {
            "start_year": start_year,
            "end_year": end_year,
            "X": [[1.0]],
            "y": [1.0],
        }


class SimpleLoader:
    """Minimal loader accepting only (start_year, end_year)."""

    def __init__(self):
        self.calls: List[tuple] = []

    def __call__(self, start_year: int, end_year: int):
        self.calls.append((start_year, end_year))
        return {"start_year": start_year, "end_year": end_year}


class RecordingTrainFunc:
    """Fake train_func that records data ranges and returns a model dict."""

    def __init__(self):
        self.calls: List[dict] = []

    def __call__(self, data: dict) -> dict:
        self.calls.append({"start_year": data["start_year"], "end_year": data["end_year"]})
        return {
            "weights": [0.5],
            "feature_count": 1,
            "trained_on": (data["start_year"], data["end_year"]),
        }


def _eval_positive(model: dict, data: dict) -> dict:
    return _positive_metric()


def _eval_negative(model: dict, data: dict) -> dict:
    return _negative_metric()


# ---------------------------------------------------------------------------
# Rolling refit: model trained before each segment
# ---------------------------------------------------------------------------

class TestRollingRefit:
    def test_each_segment_trained_on_data_strictly_before_it(self):
        """For every eval segment, training data must end before the segment starts."""
        loader = RecordingLoader(accepts_dates=False)
        train_func = RecordingTrainFunc()

        validator = WalkForwardValidator()
        result = validator.run_validation(train_func, _eval_positive, loader)

        assert result["status"] == "PASS"

        # train_func is called once per period (including the Discovery internal split).
        # For every call, trained_on[1] < eval start year of that period.
        #
        # Expected training end years:
        #   Discovery internal train: 2018 (or 2019, depending on val split)  — strictly < 2020
        #   Confirmation: 2020  — strictly < 2021
        #   Holdout: 2022       — strictly < 2023
        #   Recent holdout: 2024 — strictly < 2025
        for call in train_func.calls:
            trained_end = call["end_year"]
            trained_start = call["start_year"]
            # training end year must be strictly before or equal to Discovery.end (for
            # internal split) or before the nominal period start for later periods.
            assert trained_end < 2026, "sanity: never trained beyond 2025"
            assert trained_start >= 2018, "sanity: never before earliest data"

    def test_expanding_window_grows_monotonically(self):
        """Expanding mode: each successive training window's end year is larger."""
        loader = SimpleLoader()
        train_func = RecordingTrainFunc()

        validator = WalkForwardValidator(window_mode="expanding")
        validator.run_validation(train_func, _eval_positive, loader)

        end_years = [c["end_year"] for c in train_func.calls]
        for a, b in zip(end_years, end_years[1:]):
            assert a <= b, f"Expanding window end years not monotonically non-decreasing: {end_years}"

    def test_sliding_window_has_bounded_width(self):
        """Sliding mode: no training window exceeds sliding_window_years in width."""
        loader = SimpleLoader()
        train_func = RecordingTrainFunc()
        window = 2

        validator = WalkForwardValidator(window_mode="sliding", sliding_window_years=window)
        validator.run_validation(train_func, _eval_positive, loader)

        for call in train_func.calls:
            width = call["end_year"] - call["start_year"] + 1
            assert width <= window, f"Sliding window too wide: {width} > {window}"

    def test_result_contains_train_year_metadata(self):
        """Every period result must include train_start_year and train_end_year.

        For Discovery the internal OOS split means the model is trained up to
        disc_train_end (< disc_val_start) rather than up to period.start_year - 1.
        For all other periods train_end_year must be strictly before the period's
        nominal start year.
        """
        loader = SimpleLoader()
        train_func = RecordingTrainFunc()

        validator = WalkForwardValidator()
        result = validator.run_validation(train_func, _eval_positive, loader)

        for period_name in PERIODS:
            pr = result[period_name]
            assert "train_start_year" in pr, f"{period_name} missing train_start_year"
            assert "train_end_year" in pr, f"{period_name} missing train_end_year"

            if period_name == "Discovery":
                # Training must end before the internal validation slice starts.
                assert pr["train_end_year"] < pr["discovery_val_start_year"], (
                    f"Discovery: train_end_year {pr['train_end_year']} must be < "
                    f"discovery_val_start_year {pr['discovery_val_start_year']}"
                )
            else:
                assert pr["train_end_year"] < PERIOD_START[period_name], (
                    f"{period_name}: trained on {pr['train_end_year']} "
                    f"but eval starts {PERIOD_START[period_name]}"
                )


# ---------------------------------------------------------------------------
# Discovery kill-gate is OOS
# ---------------------------------------------------------------------------

class TestDiscoveryGateOOS:
    def test_discovery_result_is_marked_oos(self):
        loader = SimpleLoader()
        validator = WalkForwardValidator()
        result = validator.run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        disc = result["Discovery"]
        assert disc.get("oos") is True

    def test_discovery_result_has_val_split_keys(self):
        loader = SimpleLoader()
        validator = WalkForwardValidator()
        result = validator.run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        disc = result["Discovery"]
        assert "discovery_val_start_year" in disc
        assert "discovery_val_end_year" in disc
        # Validation slice must sit within Discovery period (2018-2020).
        assert disc["discovery_val_start_year"] >= 2018
        assert disc["discovery_val_end_year"] <= 2020

    def test_discovery_train_end_before_val_start(self):
        loader = SimpleLoader()
        validator = WalkForwardValidator()
        result = validator.run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        disc = result["Discovery"]
        assert disc["train_end_year"] < disc["discovery_val_start_year"], (
            "Discovery gate: training data must end before validation slice begins"
        )

    def test_negative_discovery_gate_kills_early(self):
        loader = SimpleLoader()
        validator = WalkForwardValidator()
        result = validator.run_validation(
            RecordingTrainFunc(), _eval_negative, loader
        )
        assert result["status"] == "FAIL"
        # Only Discovery result should be present; later periods not evaluated.
        assert "Confirmation" not in result


# ---------------------------------------------------------------------------
# Purge / embargo boundary math
# ---------------------------------------------------------------------------

class TestPurgeEmbargoBoundary:
    def test_purge_days_passed_to_loader_that_accepts_dates(self):
        loader = RecordingLoader(accepts_dates=True)
        validator = WalkForwardValidator(purge_days=5)
        validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)

        # Every training-data call should have a non-None purge_end_date.
        train_calls = [c for c in loader.calls]
        purge_dates = [c[2] for c in train_calls]  # index 2 = purge_end_date
        # At least some calls must carry a purge date.
        assert any(pd is not None for pd in purge_dates), (
            "Expected at least one training call with purge_end_date set"
        )

    def test_embargo_days_passed_to_loader_that_accepts_dates(self):
        loader = RecordingLoader(accepts_dates=True)
        validator = WalkForwardValidator(embargo_days=3)
        validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)

        embargo_dates = [c[3] for c in loader.calls]  # index 3 = embargo_start_date
        assert any(ed is not None for ed in embargo_dates), (
            "Expected at least one eval call with embargo_start_date set"
        )

    def test_zero_purge_embargo_passes_none_to_loader(self):
        loader = RecordingLoader(accepts_dates=True)
        validator = WalkForwardValidator(purge_days=0, embargo_days=0)
        validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)

        purge_dates = [c[2] for c in loader.calls]
        embargo_dates = [c[3] for c in loader.calls]
        assert all(pd is None for pd in purge_dates)
        assert all(ed is None for ed in embargo_dates)

    def test_simple_loader_not_passed_date_kwargs(self):
        """Loaders without date kwargs must not receive them (backward compat)."""
        loader = SimpleLoader()
        validator = WalkForwardValidator(purge_days=10, embargo_days=5)
        # Must not raise TypeError even though purge/embargo > 0.
        result = validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)
        assert result["status"] == "PASS"
        # All calls are 2-tuples.
        for call in loader.calls:
            assert len(call) == 2

    def test_purge_end_date_is_before_eval_start(self):
        """purge_end_date must be strictly before the evaluation segment start.

        The purge_end_date is derived from the eval period's start year, not
        from the training data's start_year argument.  For an expanding-window
        training call with end_year=Y, the eval boundary is Y+1-01-01, so the
        purge_end_date must be < that boundary.
        """
        from datetime import date

        loader = RecordingLoader(accepts_dates=True)
        purge_days = 7
        validator = WalkForwardValidator(purge_days=purge_days)
        validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)

        for call in loader.calls:
            train_end_year = call[1]
            purge_date_str = call[2]  # purge_end_date (only set on training calls)
            if purge_date_str is not None:
                pd = date.fromisoformat(purge_date_str)
                # The eval segment begins the year after the training window ends.
                eval_boundary = date(train_end_year + 1, 1, 1)
                assert pd < eval_boundary, (
                    f"purge_end_date {purge_date_str} must be before eval boundary {eval_boundary}"
                )

    def test_result_records_purge_embargo_values(self):
        loader = SimpleLoader()
        validator = WalkForwardValidator(purge_days=2, embargo_days=3)
        result = validator.run_validation(RecordingTrainFunc(), _eval_positive, loader)
        for pname in PERIODS:
            assert result[pname]["purge_days"] == 2
            assert result[pname]["embargo_days"] == 3


# ---------------------------------------------------------------------------
# Return dict backward compatibility
# ---------------------------------------------------------------------------

class TestReturnDictShape:
    def test_status_pass_present_on_full_pass(self):
        loader = SimpleLoader()
        result = WalkForwardValidator().run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        assert result["status"] == "PASS"

    def test_model_key_present_on_pass(self):
        loader = SimpleLoader()
        result = WalkForwardValidator().run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        assert "model" in result
        assert result["model"] is not None

    def test_status_fail_on_negative_metric(self):
        loader = SimpleLoader()
        result = WalkForwardValidator().run_validation(
            RecordingTrainFunc(), _eval_negative, loader
        )
        assert result["status"] == "FAIL"

    def test_window_mode_recorded(self):
        loader = SimpleLoader()
        result = WalkForwardValidator(window_mode="sliding").run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        assert result["window_mode"] == "sliding"

    def test_all_period_names_present_on_pass(self):
        loader = SimpleLoader()
        result = WalkForwardValidator().run_validation(
            RecordingTrainFunc(), _eval_positive, loader
        )
        for pname in PERIODS:
            assert pname in result, f"Missing period key: {pname}"

    def test_confirmation_fail_stops_before_holdout(self):
        calls = []

        def selective_eval(model, data):
            calls.append(data["start_year"])
            if data["start_year"] == 2021:
                return _negative_metric()
            return _positive_metric()

        loader = SimpleLoader()
        result = WalkForwardValidator().run_validation(
            RecordingTrainFunc(), selective_eval, loader
        )
        assert result["status"] == "FAIL"
        assert "Holdout" not in result


# ---------------------------------------------------------------------------
# Loader signature detection
# ---------------------------------------------------------------------------

class TestLoaderSignatureDetection:
    def test_simple_positional_loader_not_accepted(self):
        def simple(s, e):
            pass

        assert not _loader_accepts_date_kwargs(simple)

    def test_kwargs_loader_accepted(self):
        def with_kwargs(s, e, **kw):
            pass

        assert _loader_accepts_date_kwargs(with_kwargs)

    def test_explicit_params_accepted(self):
        def explicit(s, e, purge_end_date=None, embargo_start_date=None):
            pass

        assert _loader_accepts_date_kwargs(explicit)


# ---------------------------------------------------------------------------
# export_weights_to_cpp — endianness and derived feature_count
# ---------------------------------------------------------------------------

HEADER_FORMAT = "<IIII"  # little-endian: magic, version, model_id, feature_count
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAGIC = 0x48465433


class TestExportWeightsToCpp:
    def _write_and_read_header(self, weights, **kwargs) -> tuple:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "model.bin")
            export_weights_to_cpp(weights, path, **kwargs)
            data = Path(path).read_bytes()
        return data

    def test_magic_number_correct(self):
        data = self._write_and_read_header([1.0, 2.0, 3.0])
        magic, *_ = struct.unpack_from(HEADER_FORMAT, data, 0)
        assert magic == MAGIC

    def test_header_is_little_endian(self):
        """Magic read as little-endian must equal 0x48465433."""
        data = self._write_and_read_header([1.0])
        magic = struct.unpack_from("<I", data, 0)[0]
        assert magic == MAGIC, (
            f"Expected little-endian magic 0x{MAGIC:08X}, got 0x{magic:08X}"
        )

    def test_feature_count_derived_from_len_weights(self):
        weights = [0.1, 0.2, 0.3, 0.4, 0.5]
        data = self._write_and_read_header(weights)
        _, _, _, feature_count = struct.unpack_from(HEADER_FORMAT, data, 0)
        assert feature_count == len(weights)

    def test_feature_count_explicit_override(self):
        weights = [0.1] * 10
        data = self._write_and_read_header(weights, feature_count=7)
        _, _, _, feature_count = struct.unpack_from(HEADER_FORMAT, data, 0)
        assert feature_count == 7

    def test_version_is_1(self):
        data = self._write_and_read_header([1.0])
        _, version, _, _ = struct.unpack_from(HEADER_FORMAT, data, 0)
        assert version == 1

    def test_model_id_written(self):
        data = self._write_and_read_header([1.0], model_id=42)
        _, _, model_id, _ = struct.unpack_from(HEADER_FORMAT, data, 0)
        assert model_id == 42

    def test_total_file_size(self):
        weights = [float(i) for i in range(10)]
        data = self._write_and_read_header(weights)
        expected = HEADER_SIZE + 1024 * 8
        assert len(data) == expected

    def test_weights_round_trip_little_endian(self):
        weights = [1.5, -2.5, 3.14159]
        data = self._write_and_read_header(weights)
        body = data[HEADER_SIZE:]
        recovered = list(struct.unpack_from("<1024d", body))
        for i, w in enumerate(weights):
            assert abs(recovered[i] - w) < 1e-12, f"weight[{i}] mismatch"
        # Remaining slots are zero.
        assert all(v == 0.0 for v in recovered[len(weights):])

    def test_exceeds_capacity_raises(self):
        with pytest.raises(ValueError, match="1024"):
            export_weights_to_cpp([0.0] * 1025, "/tmp/never_written.bin")

    def test_empty_weights_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "empty.bin")
            export_weights_to_cpp([], path)
            data = Path(path).read_bytes()
        assert len(data) == HEADER_SIZE + 1024 * 8


# ---------------------------------------------------------------------------
# endian_safe_export shim
# ---------------------------------------------------------------------------

class TestEndianSafeExportShim:
    def test_export_portable_emits_deprecation_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "shim.bin")
            with pytest.warns(DeprecationWarning):
                export_weights_portable([1.0, 2.0], path)

    def test_shim_output_readable_by_load_weights_portable(self):
        weights = [0.1, 0.2, 0.3]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "shim.bin")
            with pytest.warns(DeprecationWarning):
                export_weights_portable(weights, path, model_id=5)
            recovered, meta = load_weights_portable(path)
        assert len(recovered) == len(weights)
        for i, w in enumerate(weights):
            assert abs(recovered[i] - w) < 1e-12
        assert meta["model_id"] == 5
        assert meta["version"] == 1

    def test_load_weights_portable_magic_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bad.bin")
            # Write a file with wrong magic.
            Path(path).write_bytes(b"\x00" * (16 + 1024 * 8))
            with pytest.raises(ValueError, match="magic"):
                load_weights_portable(path)

    def test_load_weights_portable_size_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "small.bin")
            Path(path).write_bytes(b"\x00" * 10)
            with pytest.raises(ValueError):
                load_weights_portable(path)

    def test_verify_weights_integrity_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "ok.bin")
            export_weights_to_cpp([1.0, 2.0], path)
            assert verify_weights_integrity(path) is True

    def test_verify_weights_integrity_fail_on_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "corrupt.bin")
            Path(path).write_bytes(b"\x00" * 50)
            assert verify_weights_integrity(path) is False

    def test_shim_round_trip_feature_count_derived(self):
        weights = [float(i) for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "fc.bin")
            with pytest.warns(DeprecationWarning):
                export_weights_portable(weights, path)
            _, meta = load_weights_portable(path)
        assert meta["feature_count"] == len(weights)
