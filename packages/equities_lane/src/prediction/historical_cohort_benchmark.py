"""Historical runner-cohort prediction benchmark for low-float equities.

This module scores real user-provided cohort labels against real user-provided
prediction rows. Cohort labels are labels only: cohort columns are never used as
prediction inputs, features, or ranking signals. Missing input files and empty
inputs fail closed; no fake benchmark data or synthetic reports are generated.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REQUIRED_COHORT_COLUMNS = (
    "ticker",
    "event_date",
    "event_start_timestamp",
    "pre_event_reference_timestamp",
    "runner_label_id",
    "event_strength",
    "max_intraday_return",
    "max_3day_return",
    "volume_expansion",
    "float_state",
    "session_type",
    "primary_catalyst_type_if_known",
    "halt_flag",
    "dilution_after_event_flag",
    "delisting_status",
)

REQUIRED_PREDICTION_COLUMNS = (
    "ticker",
    "prediction_timestamp",
    "model_id",
    "prediction_score",
    "p_run_5d",
    "p_run_3d",
    "p_run_2d",
    "p_run_1d",
    "p_afterhours_ignite",
    "p_premarket_ignite",
    "p_opening_window_ignite",
    "p_intraday_continuation",
    "expected_MFE",
    "expected_MAE",
    "probability_MFE_before_MAE",
    "expected_slippage",
    "expected_capacity",
    "p_dilution_gap",
    "p_halt_event",
    "expected_utility_by_timing_policy",
    "recommended_timing_policy",
    "timing_policy",
    "obvious_scanner_trigger_timestamp",
    "spread_cost",
    "halt_exposure",
    "dilution_exposure",
    "capacity",
    "is_hard_negative",
    "hard_negative_reason",
)

TIMESTAMP_METADATA_COLUMNS = (
    "feature_timestamp",
    "data_available_timestamp",
    "source_timestamp",
    "prediction_cutoff_timestamp",
)

REQUIRED_TIMESTAMP_PURITY_COLUMNS = (
    "data_available_timestamp",
    "prediction_cutoff_timestamp",
)

PROBABILITY_COLUMNS = (
    "prediction_score",
    "p_run_5d",
    "p_run_3d",
    "p_run_2d",
    "p_run_1d",
    "p_afterhours_ignite",
    "p_premarket_ignite",
    "p_opening_window_ignite",
    "p_intraday_continuation",
    "probability_MFE_before_MAE",
    "p_dilution_gap",
    "p_halt_event",
)

PREDICTION_SNAPSHOTS = (
    "T-10 trading days",
    "T-5 trading days",
    "T-3 trading days",
    "T-2 trading days",
    "T-1 close",
    "T-1 after-hours",
    "T0 premarket open",
    "T0 premarket mid-session",
    "T0 30 minutes before regular open",
    "T0 opening auction / opening window",
    "T0 first 1 minute",
    "T0 first 5 minutes",
    "T0 first 15 minutes",
)

TIMING_POLICIES = (
    "WATCH_ONLY",
    "SEED_T5_TO_T2",
    "ENTER_T2",
    "ENTER_T1_CLOSE",
    "ENTER_T1_AFTERHOURS",
    "ENTER_T0_PREMARKET",
    "ENTER_T0_OPENING_WINDOW",
    "ENTER_T0_INTRADAY_CONTINUATION",
    "REJECT_RISK_ADJUSTED",
)

MODEL_TOURNAMENT_ENTRIES = (
    "baseline_low_float_momentum_ranker",
    "daily_tabular_hazard_model",
    "daily_catalyst_news_filings_model",
    "daily_attention_acceleration_model",
    "daily_peer_theme_transmission_model",
    "daily_premarket_model",
    "daily_l2_l3_microstructure_model",
    "full_fused_model",
    "ablated_models",
)

ABLATION_NAMES = (
    "remove_float_supply_features",
    "remove_liquidity_fragility_features",
    "remove_order_flow_features",
    "remove_catalyst_features",
    "remove_attention_features",
    "remove_peer_theme_features",
    "remove_dilution_risk_features",
    "remove_short_borrow_features",
    "remove_l3_features",
    "remove_premarket_features",
)

PRIMARY_METRICS = (
    "precision@top_5_per_day",
    "precision@top_10_per_day",
    "precision@top_20_per_day",
    "average_rank_of_future_known_runners",
    "recall_of_known_runners_by_horizon",
    "median_lead_time_before_event",
    "mean_lead_time_before_event",
    "PR-AUC",
    "Brier score",
    "calibration error",
    "expected utility per alert",
    "MFE captured before obvious scanner trigger",
    "average MFE by alert rank",
    "average MAE by alert rank",
    "MFE-before-MAE probability",
    "slippage-adjusted expectancy",
    "dilution-adjusted expectancy",
    "halt-adjusted expectancy",
    "capacity-adjusted expectancy",
    "monthly walk-forward stability",
)

EARLY_DETECTION_METRICS = (
    "detected_T5_or_earlier",
    "detected_T3_or_earlier",
    "detected_T2_or_earlier",
    "detected_T1_close",
    "detected_afterhours",
    "detected_premarket",
    "detected_before_first_expansion_candle",
    "detected_after_first_expansion_candle",
    "useful_early_detection",
)

REQUIRED_REPORT_NAMES = (
    "annual_runner_cohort_benchmark_report.json",
    "model_tournament_report.json",
    "timing_policy_comparison_report.json",
    "early_detection_lead_time_report.json",
    "precision_at_top_n_per_day_report.json",
    "known_runner_recall_by_horizon_report.json",
    "expected_utility_report.json",
    "slippage_liquidity_capacity_report.json",
    "dilution_halt_failure_report.json",
    "feature_family_ablation_report.json",
    "l3_incremental_alpha_report.json",
    "false_positive_hard_negative_report.json",
    "final_locked_year_out_of_sample_report.json",
)

DEFAULT_CHRONOLOGICAL_EXPERIMENTS = (
    {"train_years": [2021, 2022], "validation_year": 2023, "test_year": 2024},
    {"train_years": [2021, 2022, 2023], "validation_year": 2024, "test_year": 2025},
    {"train_years": [2021, 2022, 2023, 2024], "validation_year": 2025, "test_year": "2026_YTD"},
    {"train_years": [2022, 2023], "validation_year": 2024, "test_year": 2025},
)

_PREDICTION_BASE_COLUMNS = set(REQUIRED_PREDICTION_COLUMNS) | set(TIMESTAMP_METADATA_COLUMNS) | {
    "ablation_name",
    "snapshot_name",
}


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Load CSV, JSON array, or NDJSON rows from a real non-empty file."""
    src = Path(path)
    if not src.exists():
        raise ValueError(f"input file not found: {src}")
    if src.stat().st_size == 0:
        raise ValueError(f"input file is empty: {src}")

    if src.suffix.lower() == ".csv":
        with src.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    else:
        text = src.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"input file is empty: {src}")
        try:
            data = json.loads(text)
            rows = data if isinstance(data, list) else data.get("rows", []) if isinstance(data, dict) else []
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]

    if not rows:
        raise ValueError(f"input file has no rows: {src}")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"input file must contain object rows: {src}")
    return rows


def validate_cohort_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
    _require_rows(rows, "cohort")
    missing = _missing_columns(rows, REQUIRED_COHORT_COLUMNS)
    if missing:
        raise ValueError(f"cohort rows missing required columns: {missing}")
    for i, row in enumerate(rows):
        _parse_date(row["event_date"], f"cohort[{i}].event_date")
        _parse_dt(row["event_start_timestamp"], f"cohort[{i}].event_start_timestamp")
        _parse_dt(row["pre_event_reference_timestamp"], f"cohort[{i}].pre_event_reference_timestamp")
    return {"status": "ok", "required_columns": list(REQUIRED_COHORT_COLUMNS), "n_rows": len(rows)}


def validate_prediction_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
    _require_rows(rows, "prediction")
    missing = _missing_columns(rows, REQUIRED_PREDICTION_COLUMNS)
    if missing:
        raise ValueError(f"prediction rows missing required columns: {missing}")
    missing_purity = _missing_columns(rows, REQUIRED_TIMESTAMP_PURITY_COLUMNS)
    if missing_purity:
        raise ValueError(f"prediction rows missing timestamp purity columns: {missing_purity}")

    forbidden_label_columns = sorted((set(REQUIRED_COHORT_COLUMNS) - {"ticker"}) & {k for row in rows for k in row})
    if forbidden_label_columns:
        raise ValueError(f"prediction rows contain forbidden cohort label columns: {forbidden_label_columns}")

    feature_columns = sorted({k for row in rows for k in row if k not in _PREDICTION_BASE_COLUMNS})
    seen_alerts: dict[tuple[str, str, str, str, str, str], int] = {}
    for i, row in enumerate(rows):
        prediction_ts = _parse_dt(row["prediction_timestamp"], f"prediction[{i}].prediction_timestamp")
        _validate_prediction_purity(row, i)
        _validate_probability_ranges(row, i)
        alert_key = (
            str(row["ticker"]).upper(),
            prediction_ts.isoformat(),
            str(row["model_id"]),
            str(row.get("snapshot_name") or ""),
            str(row.get("timing_policy") or row.get("recommended_timing_policy") or ""),
            str(row.get("ablation_name") or ""),
        )
        if alert_key in seen_alerts:
            raise ValueError(f"duplicate prediction alert rows: prediction[{seen_alerts[alert_key]}] and prediction[{i}]")
        seen_alerts[alert_key] = i
    return {
        "status": "ok",
        "required_columns": list(REQUIRED_PREDICTION_COLUMNS),
        "timestamp_metadata_columns": list(TIMESTAMP_METADATA_COLUMNS),
        "required_timestamp_purity_columns": list(REQUIRED_TIMESTAMP_PURITY_COLUMNS),
        "feature_columns_checked": feature_columns,
        "n_rows": len(rows),
    }


def group_annual_cohorts(cohort_rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"n_events": 0, "tickers": set()})
    for row in cohort_rows:
        year = str(_parse_date(row["event_date"], "event_date").year)
        grouped[year]["n_events"] += 1
        grouped[year]["tickers"].add(row["ticker"])
    return {
        year: {"cohort_id": f"R_{year}", "n_events": val["n_events"], "n_tickers": len(val["tickers"])}
        for year, val in sorted(grouped.items())
    }


def assign_rolling_experiments(
    cohort_rows: list[dict[str, Any]],
    locked_test_year: int | None = None,
) -> list[dict[str, Any]]:
    years_present = {_parse_date(row["event_date"], "event_date").year for row in cohort_rows}
    out = []
    for exp in DEFAULT_CHRONOLOGICAL_EXPERIMENTS:
        test_year = exp["test_year"]
        test_years = [test_year] if isinstance(test_year, int) else [int(str(test_year)[:4])]
        if locked_test_year is not None and locked_test_year not in test_years:
            continue
        train_years = list(exp["train_years"])
        validation_year = exp["validation_year"]
        leakage_ok = max(train_years + [validation_year]) < min(test_years)
        required_years = train_years + [validation_year] + test_years
        missing_years = [y for y in required_years if y not in years_present]
        out.append({
            **exp,
            "available": not missing_years,
            "missing_years": missing_years,
            "no_final_test_leakage": leakage_ok,
            "locked_final_test_year": locked_test_year,
        })
    return out


def evaluate_predictions(
    cohort_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    locked_test_year: int | None = None,
) -> dict[str, Any]:
    events = [_event(row) for row in cohort_rows]
    predictions = [_prediction(row) for row in prediction_rows]
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        matches = [_match(pred, event) for event in events if event["ticker"] == pred["ticker"]]
        matches = [m for m in matches if m]
        pre_event_matches = [m for m in matches if m["lead_time_hours"] > 0]
        best = min(pre_event_matches or matches, key=lambda m: m["days_to_event"]) if matches else None
        pred["matched_event"] = best
        pred["is_positive"] = bool(best and best["days_to_event"] <= 5 and best["lead_time_hours"] > 0)
        by_day[pred["prediction_date"]].append(pred)

    return {
        "overall_metrics": _overall_metrics(events, predictions),
        "precision_at_top_n_per_day_report": _precision_by_day(by_day),
        "runner_rank_report": _runner_ranks(events, by_day),
        "known_runner_recall_by_horizon_report": _recall_by_horizon(events, predictions),
        "early_detection_lead_time_report": _lead_time(predictions),
        "scanner_lead_time_report": _scanner_lead_time(predictions),
        "useful_early_detection_report": _useful_early_detection(predictions),
        "timing_policy_comparison_report": _aggregate(predictions, "timing_policy"),
        "model_tournament_report": _model_tournament(predictions),
        "feature_family_ablation_report": _ablation_aggregates(predictions),
        "expected_utility_report": _expected_utility_report(predictions),
        "slippage_liquidity_capacity_report": _slippage_liquidity_capacity_report(predictions),
        "dilution_halt_failure_report": _dilution_halt_failure_report(predictions),
        "l3_incremental_alpha_report": _l3_incremental_alpha_report(predictions),
        "false_positive_hard_negative_report": _false_positive_hard_negative_report(predictions),
        "final_locked_year_out_of_sample_report": _final_locked_year_report(predictions, locked_test_year),
    }


def run_historical_cohort_benchmark(
    cohort_path: str | Path,
    prediction_path: str | Path,
    output_dir: str | Path,
    locked_test_year: int | None = None,
) -> dict[str, Any]:
    cohort_rows = load_rows(cohort_path)
    prediction_rows = load_rows(prediction_path)
    cohort_validation = validate_cohort_schema(cohort_rows)
    prediction_validation = validate_prediction_schema(prediction_rows)
    annual_cohorts = group_annual_cohorts(cohort_rows)
    chronological_experiments = assign_rolling_experiments(cohort_rows, locked_test_year)
    evaluation_cohort_rows, evaluation_prediction_rows, evaluation_scope = _development_scope(
        cohort_rows,
        prediction_rows,
        locked_test_year,
    )
    reports = evaluate_predictions(evaluation_cohort_rows, evaluation_prediction_rows)
    if locked_test_year is None:
        final_reports = reports
    else:
        final_cohort_rows, final_prediction_rows = _locked_scope(cohort_rows, prediction_rows, locked_test_year)
        final_reports = evaluate_predictions(final_cohort_rows, final_prediction_rows, locked_test_year)

    report_payloads = {
        "annual_runner_cohort_benchmark_report.json": {
            "annual_cohorts": annual_cohorts,
            "chronological_experiments": chronological_experiments,
            "cohort_schema": cohort_validation,
            "evaluation_scope": evaluation_scope,
            "label_purity_invariant": "Cohort labels are labels only and are never prediction inputs.",
        },
        "model_tournament_report.json": reports["model_tournament_report"],
        "timing_policy_comparison_report.json": reports["timing_policy_comparison_report"],
        "early_detection_lead_time_report.json": reports["early_detection_lead_time_report"],
        "precision_at_top_n_per_day_report.json": reports["precision_at_top_n_per_day_report"],
        "known_runner_recall_by_horizon_report.json": reports["known_runner_recall_by_horizon_report"],
        "expected_utility_report.json": reports["expected_utility_report"],
        "slippage_liquidity_capacity_report.json": reports["slippage_liquidity_capacity_report"],
        "dilution_halt_failure_report.json": reports["dilution_halt_failure_report"],
        "feature_family_ablation_report.json": reports["feature_family_ablation_report"],
        "l3_incremental_alpha_report.json": reports["l3_incremental_alpha_report"],
        "false_positive_hard_negative_report.json": reports["false_positive_hard_negative_report"],
        "final_locked_year_out_of_sample_report.json": final_reports["final_locked_year_out_of_sample_report"],
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for report_name in REQUIRED_REPORT_NAMES:
        _write_json(out / report_name, report_payloads[report_name])

    manifest = {
        "benchmark": "historical_runner_cohort_prediction",
        "cohort_path": str(cohort_path),
        "prediction_path": str(prediction_path),
        "locked_test_year": locked_test_year,
        "reports": list(REQUIRED_REPORT_NAMES),
        "cohort_schema": cohort_validation,
        "prediction_schema": prediction_validation,
        "evaluation_scope": evaluation_scope,
        "overall_metrics": reports["overall_metrics"],
        "constants": {
            "prediction_snapshots": list(PREDICTION_SNAPSHOTS),
            "timing_policies": list(TIMING_POLICIES),
            "model_tournament_entries": list(MODEL_TOURNAMENT_ENTRIES),
            "ablation_names": list(ABLATION_NAMES),
            "primary_metrics": list(PRIMARY_METRICS),
            "early_detection_metrics": list(EARLY_DETECTION_METRICS),
        },
        "label_purity_invariant": "Cohort labels are labels only and are never prediction inputs.",
    }
    _write_json(out / "benchmark_manifest.json", manifest)
    (out / "README.md").write_text(
        "# Historical Runner-Cohort Prediction Benchmark\n\n"
        "Consumes real cohort and prediction files, validates schemas and timestamp purity, "
        "then writes additive benchmark reports. Cohort labels are labels only and must "
        "never become prediction inputs. This benchmark is a forward-cohort rare-event "
        "prediction test, not a scanner and not a retrospective pattern matcher.\n",
        encoding="utf-8",
    )
    return {"output_dir": str(out), "reports": list(REQUIRED_REPORT_NAMES), "manifest": str(out / "benchmark_manifest.json")}


def _development_scope(
    cohort_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    locked_test_year: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if locked_test_year is None:
        return cohort_rows, prediction_rows, {
            "mode": "all_rows",
            "locked_test_year": None,
            "n_excluded_locked_cohorts": 0,
            "n_excluded_locked_predictions": 0,
        }

    locked_events = [_event(row) for row in cohort_rows if _parse_date(row["event_date"], "event_date").year == locked_test_year]
    development_cohorts = [
        row for row in cohort_rows
        if _parse_date(row["event_date"], "event_date").year != locked_test_year
    ]
    development_predictions = [
        row for row in prediction_rows
        if not _prediction_in_locked_scope(row, locked_events, locked_test_year)
    ]
    return development_cohorts, development_predictions, {
        "mode": "development_rows_excluding_locked_test_year",
        "locked_test_year": locked_test_year,
        "n_excluded_locked_cohorts": len(cohort_rows) - len(development_cohorts),
        "n_excluded_locked_predictions": len(prediction_rows) - len(development_predictions),
    }


def _locked_scope(
    cohort_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    locked_test_year: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    locked_events = [_event(row) for row in cohort_rows if _parse_date(row["event_date"], "event_date").year == locked_test_year]
    locked_cohorts = [
        row for row in cohort_rows
        if _parse_date(row["event_date"], "event_date").year == locked_test_year
    ]
    locked_predictions = [
        row for row in prediction_rows
        if _prediction_in_locked_scope(row, locked_events, locked_test_year)
    ]
    return locked_cohorts, locked_predictions


def _prediction_in_locked_scope(
    row: dict[str, Any],
    locked_events: list[dict[str, Any]],
    locked_test_year: int,
) -> bool:
    prediction_ts = _parse_dt(row["prediction_timestamp"], "prediction_timestamp")
    if prediction_ts.year == locked_test_year:
        return True
    ticker = str(row["ticker"]).upper()
    return any(
        event["ticker"] == ticker and 0 <= _trading_days_between(prediction_ts.date(), event["event_date"]) <= 5
        for event in locked_events
    )


def _overall_metrics(events: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [rank["rank"] for row in _runner_ranks(events, _group_by_day(predictions)) for rank in row["ranks"]]
    lead_hours = [
        p["matched_event"]["lead_time_hours"]
        for p in predictions
        if p.get("matched_event") and p["matched_event"]["lead_time_hours"] > 0
    ]
    y_true = [1.0 if p["is_positive"] else 0.0 for p in predictions]
    y_score = [p["score"] for p in predictions]
    return {
        "n_cohort_events": len(events),
        "n_prediction_rows": len(predictions),
        "n_matched_predictions": sum(1 for p in predictions if p["is_positive"]),
        "n_hard_negatives": sum(1 for p in predictions if p["is_hard_negative"]),
        "average_rank_of_future_known_runners": sum(ranks) / len(ranks) if ranks else 0.0,
        "mean_lead_time_before_event_hours": sum(lead_hours) / len(lead_hours) if lead_hours else 0.0,
        "median_lead_time_before_event_hours": _median(lead_hours),
        "PR-AUC": _pr_auc(y_true, y_score),
        "Brier score": _brier_score(y_true, y_score),
        "calibration error": _calibration_error(y_true, y_score),
        "MFE captured before obvious scanner trigger": _avg(
            p["matched_event"]["realized_mfe"] for p in predictions
            if p["is_positive"] and (not p.get("scanner_ts") or p["prediction_timestamp"] < p["scanner_ts"])
        ),
        "average MFE by alert rank": _realized_mfe_by_rank(predictions),
        "average MAE by alert rank": _missing_realized_mae_metric(),
        "monthly walk-forward stability": _monthly_stability(predictions),
        "primary_metrics": list(PRIMARY_METRICS),
        "early_detection_metrics": list(EARLY_DETECTION_METRICS),
    }


def _require_rows(rows: list[dict[str, Any]], kind: str) -> None:
    if not rows:
        raise ValueError(f"{kind} rows are empty")


def _missing_columns(rows: list[dict[str, Any]], required: tuple[str, ...]) -> list[str]:
    return [col for col in required if any(col not in row for row in rows)]


def _validate_prediction_purity(row: dict[str, Any], i: int) -> None:
    if not row.get("data_available_timestamp") or not row.get("prediction_cutoff_timestamp"):
        raise ValueError(f"prediction[{i}] is missing data_available_timestamp or prediction_cutoff_timestamp")
    data_available = _parse_dt(row["data_available_timestamp"], f"prediction[{i}].data_available_timestamp")
    cutoff = _parse_dt(row["prediction_cutoff_timestamp"], f"prediction[{i}].prediction_cutoff_timestamp")
    prediction_ts = _parse_dt(row["prediction_timestamp"], f"prediction[{i}].prediction_timestamp")
    if cutoff > prediction_ts:
        raise ValueError(f"prediction[{i}] violates timestamp purity: prediction_cutoff_timestamp > prediction_timestamp")
    if data_available > cutoff:
        raise ValueError(f"prediction[{i}] violates timestamp purity: data_available_timestamp > prediction_cutoff_timestamp")
    for col in ("feature_timestamp", "source_timestamp"):
        if row.get(col):
            ts = _parse_dt(row[col], f"prediction[{i}].{col}")
            if ts > cutoff:
                raise ValueError(f"prediction[{i}] violates timestamp purity: {col} > prediction_cutoff_timestamp")


def _validate_probability_ranges(row: dict[str, Any], i: int) -> None:
    for col in PROBABILITY_COLUMNS:
        if row[col] in (None, ""):
            raise ValueError(f"prediction[{i}].{col} must be between 0 and 1")
        val = _float(row[col])
        if not math.isfinite(val) or val < 0.0 or val > 1.0:
            raise ValueError(f"prediction[{i}].{col} must be between 0 and 1")


def _event(row: dict[str, Any]) -> dict[str, Any]:
    event_date = _parse_date(row["event_date"], "event_date")
    return {
        "ticker": str(row["ticker"]).upper(),
        "event_date": event_date,
        "event_start": _parse_dt(row["event_start_timestamp"], "event_start_timestamp"),
        "runner_label_id": row["runner_label_id"],
        "max_intraday_return": _float(row["max_intraday_return"]),
        "max_3day_return": _float(row["max_3day_return"]),
        "volume_expansion": _float(row["volume_expansion"]),
    }


def _prediction(row: dict[str, Any]) -> dict[str, Any]:
    ts = _parse_dt(row["prediction_timestamp"], "prediction_timestamp")
    expected_mfe = _float(row["expected_MFE"])
    expected_slippage = _float(row["expected_slippage"])
    spread_cost = _float(row["spread_cost"])
    capacity_value = row["capacity"] if row["capacity"] not in (None, "") else row["expected_capacity"]
    capacity = _float(capacity_value)
    return {
        "raw": row,
        "ticker": str(row["ticker"]).upper(),
        "prediction_timestamp": ts,
        "prediction_date": ts.date().isoformat(),
        "prediction_year": ts.year,
        "model_id": str(row["model_id"]),
        "score": _float(row["prediction_score"]),
        "p_run_1d": _float(row["p_run_1d"]),
        "p_run_2d": _float(row["p_run_2d"]),
        "p_run_3d": _float(row["p_run_3d"]),
        "p_run_5d": _float(row["p_run_5d"]),
        "timing_policy": str(row.get("timing_policy") or row.get("recommended_timing_policy") or ""),
        "recommended_timing_policy": str(row.get("recommended_timing_policy") or ""),
        "snapshot_name": str(row.get("snapshot_name") or ""),
        "ablation_name": str(row.get("ablation_name") or ""),
        "expected_mfe": expected_mfe,
        "expected_mae": _float(row["expected_MAE"]),
        "p_mfe_before_mae": _float(row["probability_MFE_before_MAE"]),
        "expected_slippage": expected_slippage,
        "spread_cost": spread_cost,
        "expected_capacity": _float(row["expected_capacity"]),
        "capacity": capacity,
        "p_dilution_gap": _float(row["p_dilution_gap"]),
        "p_halt_event": _float(row["p_halt_event"]),
        "dilution_exposure": _float(row["dilution_exposure"]),
        "halt_exposure": _float(row["halt_exposure"]),
        "expected_utility": _expected_utility(row["expected_utility_by_timing_policy"], row.get("timing_policy")),
        "is_hard_negative": _bool(row.get("is_hard_negative")),
        "hard_negative_reason": str(row.get("hard_negative_reason") or ""),
        "scanner_ts": _optional_dt(row.get("obvious_scanner_trigger_timestamp")),
        "is_executable": capacity > 0 and expected_mfe > expected_slippage + spread_cost,
    }


def _match(pred: dict[str, Any], event: dict[str, Any]) -> dict[str, Any] | None:
    calendar_days = (event["event_date"] - pred["prediction_timestamp"].date()).days
    trading_days = _trading_days_between(pred["prediction_timestamp"].date(), event["event_date"])
    if trading_days < 0 or trading_days > 5:
        return None
    lead_time_hours = (event["event_start"] - pred["prediction_timestamp"]).total_seconds() / 3600.0
    return {
        "runner_label_id": event["runner_label_id"],
        "event_start": event["event_start"],
        "event_year": event["event_date"].year,
        "days_to_event": trading_days,
        "calendar_days_to_event": calendar_days,
        "lead_time_hours": lead_time_hours,
        "is_pre_event": lead_time_hours > 0,
        "realized_intraday_return": event["max_intraday_return"],
        "realized_3day_return": event["max_3day_return"],
        "realized_mfe": max(event["max_intraday_return"], event["max_3day_return"]),
    }


def _precision_by_day(by_day: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for day, preds in sorted(by_day.items()):
        ranked = sorted(preds, key=lambda p: p["score"], reverse=True)
        item = {"prediction_date": day, "n_predictions": len(ranked)}
        for k in (5, 10, 20):
            top = ranked[:k]
            item[f"precision@top_{k}_per_day"] = sum(1 for p in top if p["is_positive"]) / len(top) if top else 0.0
        rows.append(item)
    return rows


def _runner_ranks(events: list[dict[str, Any]], by_day: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        ranks = []
        for offset in range(0, 6):
            day_key = _subtract_trading_days(event["event_date"], offset).isoformat()
            ranked = sorted(by_day.get(day_key, []), key=lambda p: p["score"], reverse=True)
            for idx, pred in enumerate(ranked, start=1):
                if pred["ticker"] == event["ticker"] and pred["prediction_timestamp"] < event["event_start"]:
                    ranks.append({"prediction_date": day_key, "rank": idx, "score": pred["score"]})
        rows.append({"runner_label_id": event["runner_label_id"], "ticker": event["ticker"], "event_date": event["event_date"].isoformat(), "ranks": ranks})
    return rows


def _recall_by_horizon(events: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in (1, 2, 3, 5):
        found = set()
        score_col = f"p_run_{horizon}d"
        for pred in predictions:
            for event in events:
                if event["ticker"] != pred["ticker"]:
                    continue
                days = _trading_days_between(pred["prediction_timestamp"].date(), event["event_date"])
                if 0 <= days <= horizon and pred["prediction_timestamp"] < event["event_start"] and pred.get(score_col, 0.0) > 0 and _is_actionable_alert(pred):
                    found.add(event["runner_label_id"])
        out[f"recall_{horizon}d"] = len(found) / len(events) if events else 0.0
        out[f"events_detected_{horizon}d"] = len(found)
    return out


def _lead_time(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [p for p in predictions if p.get("matched_event")]
    hours = [p["matched_event"]["lead_time_hours"] for p in matched if p["matched_event"]["lead_time_hours"] > 0]
    return {
        "n_detected": len(hours),
        "n_matched_predictions": len(matched),
        "mean_lead_time_hours": sum(hours) / len(hours) if hours else 0.0,
        "median_lead_time_hours": _median(hours),
        "detected_T5_or_earlier": sum(1 for h in hours if h >= 5 * 24),
        "detected_T3_or_earlier": sum(1 for h in hours if h >= 3 * 24),
        "detected_T2_or_earlier": sum(1 for h in hours if h >= 2 * 24),
        "detected_T1_close": sum(1 for p in matched if "T-1 close" in p["snapshot_name"] or p["timing_policy"] == "ENTER_T1_CLOSE"),
        "detected_afterhours": sum(1 for p in matched if "after" in p["snapshot_name"].lower() or p["timing_policy"] == "ENTER_T1_AFTERHOURS"),
        "detected_premarket": sum(1 for p in matched if "premarket" in p["snapshot_name"].lower() or p["timing_policy"] == "ENTER_T0_PREMARKET"),
        "detected_before_first_expansion_candle": sum(1 for p in matched if p["prediction_timestamp"] < p["matched_event"]["event_start"]),
        "detected_after_first_expansion_candle": sum(1 for p in matched if p["prediction_timestamp"] >= p["matched_event"]["event_start"]),
    }


def _scanner_lead_time(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    vals = []
    for pred in predictions:
        match = pred.get("matched_event")
        if match and pred.get("scanner_ts"):
            vals.append((pred["prediction_timestamp"] - pred["scanner_ts"]).total_seconds() / 3600.0)
    return {
        "definition": "model_lead_time_vs_scanner = timestamp_model_alert - timestamp_obvious_scanner_alert; negative is earlier than scanner",
        "n_with_scanner_timestamp": len(vals),
        "mean_model_lead_time_vs_scanner_hours": sum(vals) / len(vals) if vals else 0.0,
        "n_model_alerts_before_scanner": sum(1 for v in vals if v < 0),
    }


def _useful_early_detection(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    useful = 0
    total = 0
    for pred in predictions:
        match = pred.get("matched_event")
        if not match:
            continue
        total += 1
        before_scanner = not pred.get("scanner_ts") or pred["prediction_timestamp"] < pred["scanner_ts"]
        if match["lead_time_hours"] > 0 and before_scanner and pred["expected_utility"] > 0 and pred["is_executable"]:
            useful += 1
    return {
        "definition": "alert before obvious scanner trigger, positive expected utility, and executable under spread/slippage/capacity constraints",
        "n_useful_early_detections": useful,
        "n_detected": total,
        "useful_early_detection_rate": useful / total if total else 0.0,
    }


def _aggregate(predictions: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        grouped[pred.get(key) or "UNKNOWN"].append(pred)
    return {name: _summary(rows) for name, rows in sorted(grouped.items())}


def _model_tournament(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _aggregate(predictions, "model_id")
    for name in MODEL_TOURNAMENT_ENTRIES:
        grouped.setdefault(name, {"metadata": {"status": "missing_data"}, "n_predictions": 0})
    return grouped


def _ablation_aggregates(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _aggregate([p for p in predictions if p.get("ablation_name")], "ablation_name")
    for name in ABLATION_NAMES:
        grouped.setdefault(name, {"metadata": {"status": "missing_data"}, "n_predictions": 0})
    return grouped


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = sum(1 for p in rows if p["is_positive"])
    hard_negatives = sum(1 for p in rows if p["is_hard_negative"])
    return {
        "metadata": {"status": "ok"},
        "n_predictions": len(rows),
        "n_positive_matches": positives,
        "n_hard_negatives": hard_negatives,
        "hit_rate": positives / len(rows) if rows else 0.0,
        "average_future_MFE": _avg(_realized_mfe_or_zero(p) for p in rows),
        "average_future_MAE": None,
        "future_MAE_status": _missing_realized_mae_metric(),
        "average_expected_MFE": _avg(p["expected_mfe"] for p in rows),
        "average_expected_MAE": _avg(p["expected_mae"] for p in rows),
        "MFE_before_MAE_probability": _avg(p["p_mfe_before_mae"] for p in rows),
        "average_hours_early": _avg(p["matched_event"]["lead_time_hours"] for p in rows if p.get("matched_event")),
        "entry_slippage": _avg(p["expected_slippage"] for p in rows),
        "spread_cost": _avg(p["spread_cost"] for p in rows),
        "halt_exposure": _avg(p["halt_exposure"] for p in rows),
        "dilution_exposure": _avg(p["dilution_exposure"] for p in rows),
        "capacity": _avg(p["capacity"] for p in rows),
        "net_expected_utility": _avg(p["expected_utility"] for p in rows),
        "avg_score": _avg(p["score"] for p in rows),
    }


def _is_actionable_alert(pred: dict[str, Any]) -> bool:
    return pred["score"] > 0 and pred["expected_utility"] > 0 and pred["is_executable"] and pred["timing_policy"] != "REJECT_RISK_ADJUSTED"


def _pr_auc(y_true: list[float], y_score: list[float]) -> float:
    positives = sum(y_true)
    if positives == 0:
        return 0.0
    pairs = sorted(zip(y_score, y_true), reverse=True)
    tp = 0.0
    fp = 0.0
    prev_recall = 0.0
    area = 0.0
    for _, label in pairs:
        if label:
            tp += 1.0
        else:
            fp += 1.0
        recall = tp / positives
        precision = tp / (tp + fp)
        area += precision * max(0.0, recall - prev_recall)
        prev_recall = recall
    return area


def _brier_score(y_true: list[float], y_score: list[float]) -> float:
    return _avg((score - label) ** 2 for score, label in zip(y_score, y_true))


def _calibration_error(y_true: list[float], y_score: list[float], bins: int = 10) -> float:
    errors = []
    for idx in range(bins):
        low = idx / bins
        high = (idx + 1) / bins
        bucket = [(score, label) for score, label in zip(y_score, y_true) if low <= score < high or (idx == bins - 1 and score == 1.0)]
        if bucket:
            errors.append(abs(_avg(score for score, _ in bucket) - _avg(label for _, label in bucket)))
    return _avg(errors)


def _realized_mfe_by_rank(predictions: list[dict[str, Any]]) -> dict[str, float]:
    ranked = sorted(predictions, key=lambda p: p["score"], reverse=True)
    return {
        "top_5": _avg(_realized_mfe_or_zero(p) for p in ranked[:5]),
        "top_10": _avg(_realized_mfe_or_zero(p) for p in ranked[:10]),
        "top_20": _avg(_realized_mfe_or_zero(p) for p in ranked[:20]),
    }


def _realized_mfe_or_zero(pred: dict[str, Any]) -> float:
    if pred.get("is_positive") and pred.get("matched_event"):
        return float(pred["matched_event"]["realized_mfe"])
    return 0.0


def _missing_realized_mae_metric() -> dict[str, str]:
    return {
        "status": "missing_data",
        "reason": "Required cohort schema does not include a realized MAE label column.",
    }


def _monthly_stability(predictions: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        grouped[pred["prediction_timestamp"].strftime("%Y-%m")].append(pred)
    return {
        month: {
            "n_alerts": float(len(rows)),
            "hit_rate": _avg(1.0 if p["is_positive"] else 0.0 for p in rows),
            "expected_utility_per_alert": _avg(p["expected_utility"] for p in rows),
        }
        for month, rows in sorted(grouped.items())
    }


def _expected_utility_report(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n_alerts": len(predictions),
        "expected_utility_per_alert": _avg(p["expected_utility"] for p in predictions),
        "positive_expected_utility_rate": _avg(1.0 if p["expected_utility"] > 0 else 0.0 for p in predictions),
        "by_timing_policy": _aggregate(predictions, "timing_policy"),
    }


def _slippage_liquidity_capacity_report(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "average_expected_slippage": _avg(p["expected_slippage"] for p in predictions),
        "average_spread_cost": _avg(p["spread_cost"] for p in predictions),
        "average_capacity": _avg(p["capacity"] for p in predictions),
        "executable_alerts": sum(1 for p in predictions if p["is_executable"]),
        "capacity_adjusted_expectancy": _avg(p["expected_utility"] for p in predictions if p["capacity"] > 0),
    }


def _dilution_halt_failure_report(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [p for p in predictions if not p["is_positive"] and (p["p_dilution_gap"] > 0 or p["p_halt_event"] > 0)]
    return {
        "n_dilution_or_halt_exposed_false_positives": len(failures),
        "average_p_dilution_gap": _avg(p["p_dilution_gap"] for p in predictions),
        "average_p_halt_event": _avg(p["p_halt_event"] for p in predictions),
        "dilution_adjusted_expectancy": _avg(p["expected_utility"] - p["dilution_exposure"] for p in predictions),
        "halt_adjusted_expectancy": _avg(p["expected_utility"] - p["halt_exposure"] for p in predictions),
    }


def _l3_incremental_alpha_report(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    l3_rows = [p for p in predictions if "l3" in p["model_id"].lower()]
    if not l3_rows:
        return {"metadata": {"status": "missing_data", "reason": "No L3 model rows were present."}}
    non_l3_rows = [p for p in predictions if "l3" not in p["model_id"].lower()]
    if not non_l3_rows:
        return {
            "metadata": {"status": "missing_data", "reason": "No non-L3 comparison rows were present."},
            "l3_summary": _summary(l3_rows),
            "non_l3_summary": {"metadata": {"status": "missing_data"}, "n_predictions": 0},
            "incremental_expected_utility": None,
        }
    return {
        "metadata": {"status": "ok"},
        "l3_summary": _summary(l3_rows),
        "non_l3_summary": _summary(non_l3_rows),
        "incremental_expected_utility": _avg(p["expected_utility"] for p in l3_rows) - _avg(p["expected_utility"] for p in non_l3_rows),
    }


def _false_positive_hard_negative_report(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    false_positives = [p for p in predictions if not p["is_positive"]]
    hard = [p for p in false_positives if p["is_hard_negative"]]
    reasons = Counter(p["hard_negative_reason"] or "unspecified" for p in hard)
    return {
        "n_false_positives": len(false_positives),
        "n_hard_negative_false_positives": len(hard),
        "hard_negative_reason_counts": dict(sorted(reasons.items())),
        "average_false_positive_score": _avg(p["score"] for p in false_positives),
    }


def _final_locked_year_report(predictions: list[dict[str, Any]], locked_test_year: int | None) -> dict[str, Any]:
    if locked_test_year is None:
        return {"metadata": {"status": "not_locked", "reason": "No locked test year was provided."}}
    rows = [
        p for p in predictions
        if p["prediction_year"] == locked_test_year or (p.get("matched_event") and p["matched_event"]["event_year"] == locked_test_year)
    ]
    return {
        "metadata": {"status": "ok", "locked_test_year": locked_test_year, "all_alerts_included": True},
        "n_alerts": len(rows),
        "summary": _summary(rows) if rows else {"metadata": {"status": "missing_data"}, "n_predictions": 0},
        "alerts": [_alert_row(p) for p in sorted(rows, key=lambda x: x["prediction_timestamp"])],
    }


def _alert_row(pred: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": pred["ticker"],
        "prediction_timestamp": pred["prediction_timestamp"].isoformat(),
        "model_id": pred["model_id"],
        "score": pred["score"],
        "is_positive": pred["is_positive"],
        "is_hard_negative": pred["is_hard_negative"],
        "expected_utility": pred["expected_utility"],
        "timing_policy": pred["timing_policy"],
    }


def _group_by_day(predictions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        out[pred["prediction_date"]].append(pred)
    return out


def _trading_days_between(start: date, end: date) -> int:
    if start > end:
        return -_trading_days_between(end, start)
    if start == end:
        return 0
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def _subtract_trading_days(value: date, days: int) -> date:
    current = value
    remaining = days
    while remaining > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _parse_date(value: Any, field: str) -> date:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError as exc:
        raise ValueError(f"invalid date in {field}: {value}") from exc


def _parse_dt(value: Any, field: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError(f"missing timestamp in {field}")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp in {field}: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _optional_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_dt(value, "optional_timestamp")


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _expected_utility(value: Any, policy: Any) -> float:
    if isinstance(value, dict):
        if policy in value:
            return _float(value[policy])
        return max((_float(v) for v in value.values()), default=0.0)
    text = str(value).strip()
    if not text:
        return 0.0
    if text.startswith("{"):
        try:
            return _expected_utility(json.loads(text), policy)
        except json.JSONDecodeError:
            return 0.0
    return _float(text)


def _avg(values: Any) -> float:
    vals = [float(v) for v in values]
    return sum(vals) / len(vals) if vals else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
