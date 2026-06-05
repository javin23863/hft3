"""Valid-trading-day coverage checks before robustness."""

from __future__ import annotations

from datetime import date, timedelta

from workbench.src.data.coverage_check import (
    MIN_VALID_TRADING_DAYS,
    OPTIONS_MIN_VALID_DAYS,
    TARGET_VALID_TRADING_DAYS,
    build_coverage_summary_from_dates,
)


def _days(count: int, *, start: date = date(2024, 1, 2)) -> set[date]:
    days: set[date] = set()
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.add(cursor)
        cursor += timedelta(days=1)
    return days


def test_coverage_status_boundaries():
    below = build_coverage_summary_from_dates(
        model_name="GHOST_ROUTE",
        data_type="CME MBO Level 3",
        required_symbols=["ES"],
        symbol_dates={"ES": _days(MIN_VALID_TRADING_DAYS - 1)},
    )
    minimum = build_coverage_summary_from_dates(
        model_name="GHOST_ROUTE",
        data_type="CME MBO Level 3",
        required_symbols=["ES"],
        symbol_dates={"ES": _days(MIN_VALID_TRADING_DAYS)},
    )
    target = build_coverage_summary_from_dates(
        model_name="GHOST_ROUTE",
        data_type="CME MBO Level 3",
        required_symbols=["ES"],
        symbol_dates={"ES": _days(TARGET_VALID_TRADING_DAYS)},
    )

    assert below.coverage_status == "BELOW_MINIMUM"
    assert minimum.coverage_status == "MINIMUM_ONLY"
    assert target.coverage_status == "TARGET_MET"


def test_pair_model_requires_each_side_and_overlap():
    es_days = _days(300)
    mes_days = set(sorted(es_days)[:249])
    summary = build_coverage_summary_from_dates(
        model_name="GHOST_ROUTE",
        data_type="CME MBO Level 3",
        required_symbols=["ES", "MES"],
        symbol_dates={"ES": es_days, "MES": mes_days},
    )

    assert summary.coverage_status == "BELOW_MINIMUM"
    assert summary.overlap_required is True
    assert summary.overlap_valid_trading_days == 249
    assert {row.symbol: row.valid_trading_days for row in summary.per_symbol} == {
        "ES": 300,
        "MES": 249,
    }


def test_options_model_requires_underlying_and_option_days():
    underlying = _days(300)
    option_days = _days(OPTIONS_MIN_VALID_DAYS - 1)
    summary = build_coverage_summary_from_dates(
        model_name="DEALER_HEDGING",
        data_type="0DTE options and underlying intraday",
        required_symbols=["ES"],
        symbol_dates={"ES": underlying},
        option_dates=option_days,
    )

    assert summary.coverage_status == "BELOW_MINIMUM"
    assert summary.valid_trading_days == 300
    assert summary.option_valid_trading_days == OPTIONS_MIN_VALID_DAYS - 1
    assert summary.option_minimum_required_days == OPTIONS_MIN_VALID_DAYS
