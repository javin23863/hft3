"""Valid-trading-day coverage checks before robustness."""

from __future__ import annotations

from datetime import date, timedelta

from workbench.src.data.coverage_check import (
    MIN_VALID_TRADING_DAYS,
    OPTIONS_MIN_VALID_DAYS,
    TARGET_VALID_TRADING_DAYS,
    _event_has_own_official_npz,
    _option_dates,
    _raw_backlog_dates_for_symbol,
    _runnable_npz_dates_for_symbol,
    build_coverage_summary_from_dates,
    data_type_for_model,
    required_symbols_for_model,
)
from workbench.src.data.event_catalog import EventSpec


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


def test_named_pair_models_do_not_inherit_wrong_anchor_symbol():
    assert required_symbols_for_model("ES_MES_LEAD_LAG", "NQ.v.0") == ["ES", "MES"]
    assert required_symbols_for_model("NQ_MNQ_LEAD_LAG", "MES.v.0") == ["NQ", "MNQ"]


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


def test_official_coverage_counts_only_runnable_mbo_npz(tmp_path):
    npz = tmp_path / "data" / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    raw = tmp_path / "data" / "replay" / "mbp10" / "MES.v.0_CPI_2025_03_12_TIGHT_mbp-10.dbn.zst"
    root_raw = tmp_path / "data" / "MES.v.0_NFP_2025_04_04_TIGHT_mbo.dbn.zst"
    npz.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    root_raw.parent.mkdir(parents=True, exist_ok=True)
    npz.write_bytes(b"npz-placeholder")
    raw.write_bytes(b"raw-placeholder")
    root_raw.write_bytes(b"raw-placeholder")

    assert _runnable_npz_dates_for_symbol(tmp_path, "MES") == {date(2025, 2, 12)}
    assert _raw_backlog_dates_for_symbol(tmp_path, "MES") == {
        date(2025, 3, 12),
        date(2025, 4, 4),
    }


def test_raw_download_days_are_reported_but_do_not_inflate_valid_days():
    runnable = {date(2025, 2, 12)}
    raw = {date(2025, 2, 12), date(2025, 3, 12)}
    summary = build_coverage_summary_from_dates(
        model_name="HYP_5",
        data_type="CME MBO Level 3",
        required_symbols=["MES"],
        symbol_dates={"MES": runnable},
        raw_backlog_dates={"MES": raw},
    )

    assert summary.valid_trading_days == 1
    assert summary.runnable_npz_days == 1
    assert summary.raw_download_days == 2
    assert summary.missing_conversion_days == 1
    assert summary.official_coverage_status == "BELOW_MINIMUM"


def test_catalog_fallback_npz_does_not_count_as_requested_symbol_coverage(tmp_path):
    event_id = "CPI_2018_01_11_TIGHT"
    es_path = tmp_path / "data" / "npz" / f"ES.v.0_{event_id}_mbo.npz"
    es_path.parent.mkdir(parents=True)
    es_path.write_bytes(b"npz-placeholder")
    event = EventSpec(
        event_id=event_id,
        event_type="CPI",
        release_date="2018-01-11",
        event_context="CPI_TIGHT",
        symbol="MES.v.0",
        npz_path=es_path,
        npz_present=True,
        start_utc=None,
        end_utc=None,
        npz_symbol_used="ES.v.0",
    )

    assert not _event_has_own_official_npz(tmp_path, event, "MES.v.0")
    assert _event_has_own_official_npz(tmp_path, event, "ES.v.0")


def test_data_type_for_model_fixing_mbo_returns_expiry_day_label():
    assert data_type_for_model("DEALER_HEDGING", {"required_datasets": ["fixing_mbo"]}) == "CME options expiry-day datasets"


def test_option_dates_includes_lake_root_options_and_both_filename_patterns(tmp_path, monkeypatch):
    npz_dir = tmp_path / "npz"
    npz_dir.mkdir()
    monkeypatch.setenv("HFT3_NPZ_ROOT", str(npz_dir))

    options_dir = tmp_path / "options" / "fixing_mbo"
    options_dir.mkdir(parents=True)
    (options_dir / "ES_fixing_2025-01-03.dbn.zst").write_bytes(b"x")
    (options_dir / "ES_fixing_trades_2025-01-06.dbn.zst").write_bytes(b"x")

    from datetime import date

    found = _option_dates(tmp_path)
    assert date(2025, 1, 3) in found
    assert date(2025, 1, 6) in found
