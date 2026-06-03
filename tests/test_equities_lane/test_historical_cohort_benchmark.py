"""Historical cohort benchmark logic tests."""
from __future__ import annotations

import csv
import json

import pytest

from equities_lane.src.prediction.historical_cohort_benchmark import (
    REQUIRED_REPORT_NAMES,
    assign_rolling_experiments,
    evaluate_predictions,
    run_historical_cohort_benchmark,
    validate_prediction_schema,
)


def _cohort(**overrides):
    row = {
        "ticker": "ABCD",
        "event_date": "2024-01-05",
        "event_start_timestamp": "2024-01-05T14:30:00",
        "pre_event_reference_timestamp": "2024-01-04T20:00:00",
        "runner_label_id": "ABCD-2024-01-05",
        "event_strength": "strong",
        "max_intraday_return": "1.25",
        "max_3day_return": "1.80",
        "volume_expansion": "8.0",
        "float_state": "low_float",
        "session_type": "regular",
        "primary_catalyst_type_if_known": "news",
        "halt_flag": "false",
        "dilution_after_event_flag": "false",
        "delisting_status": "listed",
    }
    row.update(overrides)
    return row


def _prediction(**overrides):
    row = {
        "ticker": "ABCD",
        "prediction_timestamp": "2024-01-04T10:00:00",
        "data_available_timestamp": "2024-01-04T09:59:00",
        "prediction_cutoff_timestamp": "2024-01-04T10:00:00",
        "model_id": "baseline",
        "prediction_score": "0.91",
        "p_run_5d": "0.91",
        "p_run_3d": "0.80",
        "p_run_2d": "0.70",
        "p_run_1d": "0.60",
        "p_afterhours_ignite": "0.1",
        "p_premarket_ignite": "0.1",
        "p_opening_window_ignite": "0.1",
        "p_intraday_continuation": "0.1",
        "expected_MFE": "1.0",
        "expected_MAE": "0.2",
        "probability_MFE_before_MAE": "0.7",
        "expected_slippage": "0.02",
        "expected_capacity": "10000",
        "p_dilution_gap": "0.05",
        "p_halt_event": "0.04",
        "expected_utility_by_timing_policy": "0.3",
        "recommended_timing_policy": "ENTER_T1_CLOSE",
        "timing_policy": "ENTER_T1_CLOSE",
        "obvious_scanner_trigger_timestamp": "2024-01-04T12:00:00",
        "spread_cost": "0.01",
        "halt_exposure": "0.04",
        "dilution_exposure": "0.05",
        "capacity": "10000",
        "is_hard_negative": "false",
        "hard_negative_reason": "",
    }
    row.update(overrides)
    return row


def test_prediction_timestamp_purity_fails_closed():
    row = _prediction(
        my_feature="12",
        data_available_timestamp="2024-01-04T10:05:00",
        prediction_cutoff_timestamp="2024-01-04T10:00:00",
    )
    with pytest.raises(ValueError, match="timestamp purity"):
        validate_prediction_schema([row])


def test_prediction_cutoff_after_prediction_fails_closed():
    row = _prediction(
        my_feature="12",
        data_available_timestamp="2024-01-04T10:00:00",
        prediction_cutoff_timestamp="2024-01-04T10:01:00",
    )
    with pytest.raises(ValueError, match="prediction_cutoff_timestamp > prediction_timestamp"):
        validate_prediction_schema([row])


def test_prediction_rows_require_timestamp_purity_columns():
    row = _prediction()
    del row["data_available_timestamp"]

    with pytest.raises(ValueError, match="timestamp purity columns"):
        validate_prediction_schema([row])


def test_prediction_rows_reject_cohort_label_columns():
    row = _prediction(event_date="2024-01-05")
    with pytest.raises(ValueError, match="forbidden cohort label columns"):
        validate_prediction_schema([row])


def test_probability_columns_must_be_in_range():
    row = _prediction(prediction_score="1.01")

    with pytest.raises(ValueError, match="prediction_score must be between 0 and 1"):
        validate_prediction_schema([row])


def test_probability_columns_reject_nan():
    row = _prediction(prediction_score="NaN")

    with pytest.raises(ValueError, match="prediction_score must be between 0 and 1"):
        validate_prediction_schema([row])


def test_probability_columns_reject_blank_values():
    row = _prediction(prediction_score="")

    with pytest.raises(ValueError, match="prediction_score must be between 0 and 1"):
        validate_prediction_schema([row])


def test_duplicate_prediction_alert_rows_fail_closed():
    with pytest.raises(ValueError, match="duplicate prediction alert rows"):
        validate_prediction_schema([_prediction(), _prediction()])


def test_evaluate_predictions_precision_rank_recall_and_lead_time():
    rows = [
        _prediction(ticker="ZZZZ", prediction_score="0.95", is_hard_negative="true", hard_negative_reason="trap"),
        _prediction(),
    ]

    result = evaluate_predictions([_cohort()], rows)

    day = result["precision_at_top_n_per_day_report"][0]
    assert day["precision@top_5_per_day"] == 0.5
    assert result["runner_rank_report"][0]["ranks"][0]["rank"] == 2
    assert result["known_runner_recall_by_horizon_report"]["recall_1d"] == 1.0
    assert result["overall_metrics"]["n_hard_negatives"] == 1
    assert result["early_detection_lead_time_report"]["detected_before_first_expansion_candle"] == 1
    assert result["useful_early_detection_report"]["useful_early_detection_rate"] == 1.0


def test_realized_mfe_metrics_use_cohort_outcomes_not_expected_values():
    result = evaluate_predictions([
        _cohort(max_intraday_return="1.5", max_3day_return="2.5")
    ], [_prediction(expected_MFE="99.0")])

    overall = result["overall_metrics"]
    assert overall["MFE captured before obvious scanner trigger"] == 2.5
    assert overall["average MFE by alert rank"]["top_5"] == 2.5
    assert overall["average MAE by alert rank"]["status"] == "missing_data"


def test_post_event_prediction_is_not_positive():
    rows = [_prediction(prediction_timestamp="2024-01-05T15:00:00")]

    result = evaluate_predictions([_cohort()], rows)

    assert result["overall_metrics"]["n_matched_predictions"] == 0
    assert result["precision_at_top_n_per_day_report"][0]["precision@top_5_per_day"] == 0.0
    assert result["early_detection_lead_time_report"]["detected_after_first_expansion_candle"] == 1


def test_same_ticker_post_event_match_does_not_hide_future_pre_event_match():
    stale_event = _cohort(
        event_date="2024-01-04",
        event_start_timestamp="2024-01-04T09:30:00",
        runner_label_id="ABCD-2024-01-04",
    )

    result = evaluate_predictions([stale_event, _cohort()], [_prediction(prediction_timestamp="2024-01-04T10:00:00")])

    assert result["overall_metrics"]["n_matched_predictions"] == 1
    assert result["early_detection_lead_time_report"]["mean_lead_time_hours"] > 0


def test_horizon_matching_uses_weekday_trading_days():
    event = _cohort(
        event_date="2024-01-08",
        event_start_timestamp="2024-01-08T14:30:00",
        runner_label_id="ABCD-2024-01-08",
    )
    pred = _prediction(
        prediction_timestamp="2024-01-01T10:00:00",
        data_available_timestamp="2024-01-01T09:59:00",
        prediction_cutoff_timestamp="2024-01-01T10:00:00",
    )

    result = evaluate_predictions([event], [pred])

    assert result["known_runner_recall_by_horizon_report"]["recall_5d"] == 1.0


def test_numeric_zero_capacity_is_not_executable():
    result = evaluate_predictions([_cohort()], [_prediction(capacity=0, expected_capacity=10000)])

    assert result["known_runner_recall_by_horizon_report"]["recall_1d"] == 0.0
    assert result["useful_early_detection_report"]["useful_early_detection_rate"] == 0.0


def test_l3_incremental_alpha_requires_non_l3_baseline():
    result = evaluate_predictions([_cohort()], [_prediction(model_id="daily_l2_l3_microstructure_model")])

    report = result["l3_incremental_alpha_report"]

    assert report["metadata"]["status"] == "missing_data"
    assert report["incremental_expected_utility"] is None


def test_chronological_experiment_availability_requires_all_years():
    experiments = assign_rolling_experiments([_cohort()], locked_test_year=2024)

    assert experiments[0]["available"] is False
    assert experiments[0]["missing_years"] == [2021, 2022, 2023]


def test_run_writes_required_reports_and_manifest(tmp_path):
    cohort_path = tmp_path / "cohorts.csv"
    prediction_path = tmp_path / "predictions.json"
    output = tmp_path / "out"

    with cohort_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(_cohort().keys()))
        writer.writeheader()
        writer.writerow(_cohort())
    prediction_path.write_text(json.dumps([_prediction()]), encoding="utf-8")

    result = run_historical_cohort_benchmark(cohort_path, prediction_path, output, locked_test_year=2024)

    assert sorted(p.name for p in output.glob("*.json")) == sorted([*REQUIRED_REPORT_NAMES, "benchmark_manifest.json"])
    assert (output / "README.md").exists()
    manifest = json.loads((output / "benchmark_manifest.json").read_text(encoding="utf-8"))
    assert manifest["label_purity_invariant"] == "Cohort labels are labels only and are never prediction inputs."
    assert result["output_dir"] == str(output)
    model_report = json.loads((output / "model_tournament_report.json").read_text(encoding="utf-8"))
    assert model_report["daily_l2_l3_microstructure_model"]["metadata"]["status"] == "missing_data"


def test_locked_year_is_excluded_from_non_final_reports(tmp_path):
    cohort_path = tmp_path / "cohorts.csv"
    prediction_path = tmp_path / "predictions.json"
    output = tmp_path / "out"
    locked_cohort = _cohort(
        event_date="2025-01-03",
        event_start_timestamp="2025-01-03T14:30:00",
        runner_label_id="ABCD-2025-01-03",
    )
    locked_prediction = _prediction(
        prediction_timestamp="2024-12-31T10:00:00",
        data_available_timestamp="2024-12-31T09:59:00",
        prediction_cutoff_timestamp="2024-12-31T10:00:00",
        model_id="locked_year_alert",
    )

    with cohort_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(_cohort().keys()))
        writer.writeheader()
        writer.writerow(_cohort())
        writer.writerow(locked_cohort)
    prediction_path.write_text(json.dumps([_prediction(), locked_prediction]), encoding="utf-8")

    run_historical_cohort_benchmark(cohort_path, prediction_path, output, locked_test_year=2025)

    manifest = json.loads((output / "benchmark_manifest.json").read_text(encoding="utf-8"))
    final_report = json.loads((output / "final_locked_year_out_of_sample_report.json").read_text(encoding="utf-8"))
    assert manifest["overall_metrics"]["n_prediction_rows"] == 1
    assert manifest["evaluation_scope"]["n_excluded_locked_predictions"] == 1
    assert final_report["n_alerts"] == 1


def test_locked_report_keeps_year_end_alert_when_prior_event_is_closer(tmp_path):
    cohort_path = tmp_path / "cohorts.csv"
    prediction_path = tmp_path / "predictions.json"
    output = tmp_path / "out"
    prior_event = _cohort(
        event_date="2024-12-31",
        event_start_timestamp="2024-12-31T15:00:00",
        runner_label_id="ABCD-2024-12-31",
    )
    locked_event = _cohort(
        event_date="2025-01-03",
        event_start_timestamp="2025-01-03T14:30:00",
        runner_label_id="ABCD-2025-01-03",
    )
    locked_prediction = _prediction(
        prediction_timestamp="2024-12-31T10:00:00",
        data_available_timestamp="2024-12-31T09:59:00",
        prediction_cutoff_timestamp="2024-12-31T10:00:00",
        model_id="year_end_locked_alert",
    )

    with cohort_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(_cohort().keys()))
        writer.writeheader()
        writer.writerow(prior_event)
        writer.writerow(locked_event)
    prediction_path.write_text(json.dumps([locked_prediction]), encoding="utf-8")

    run_historical_cohort_benchmark(cohort_path, prediction_path, output, locked_test_year=2025)

    final_report = json.loads((output / "final_locked_year_out_of_sample_report.json").read_text(encoding="utf-8"))
    assert final_report["n_alerts"] == 1
    assert final_report["summary"]["n_positive_matches"] == 1


def test_missing_file_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="input file not found"):
        run_historical_cohort_benchmark(tmp_path / "missing.csv", tmp_path / "predictions.json", tmp_path / "out")
