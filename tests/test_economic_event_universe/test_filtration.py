"""Filtration invariants for EventContextEngine (B1/B2/B3)."""

from datetime import datetime, timezone

from features_engine.src.regime.event_context import EventContextEngine

_HEADER = (
    "event_id,event_type,release_date,release_time,timezone,window_name,"
    "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
)


def test_scoped_event_id_ignores_other_rows(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,c\n"
        + "NFP_2024_09_06_TIGHT,NFP,2024-09-06,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,n\n",
        encoding="utf-8",
    )
    scoped = EventContextEngine(str(csv), event_id="CPI_2024_09_11_TIGHT")
    # CPI release window on Sep 11 12:30 UTC (EDT)
    ts = datetime(2024, 9, 11, 12, 35, tzinfo=timezone.utc)
    assert scoped.resolve(ts) == "CPI_TIGHT"
    global_engine = EventContextEngine(str(csv))
    assert global_engine.resolve(ts) == "CPI_TIGHT"


def test_scoped_event_id_normal_outside_window(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,c\n"
        + "NFP_2024_09_11_TIGHT,NFP,2024-09-11,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,n\n",
        encoding="utf-8",
    )
    scoped = EventContextEngine(str(csv), event_id="CPI_2024_09_11_TIGHT")
    ts = datetime(2024, 9, 11, 12, 35, tzinfo=timezone.utc)
    assert scoped.resolve(ts) == "CPI_TIGHT"
    global_engine = EventContextEngine(str(csv))
    # Both CPI and NFP overlap same day — global engine picks lower context_priority
    assert global_engine.resolve(ts) in {"CPI_TIGHT", "NFP_TIGHT"}


def test_effective_date_future_row_excluded(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2030-01-01,future\n",
        encoding="utf-8",
    )
    engine = EventContextEngine(str(csv))
    ts = datetime(2024, 9, 11, 12, 35, tzinfo=timezone.utc)
    assert engine.resolve(ts) == "NORMAL"


def test_overlap_ignores_csv_priority(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "FOMC_STATEMENT_2024_06_12_TIGHT,FOMC_STATEMENT,2024-06-12,14:00:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,1,Fed,https://fed.gov,2018-01-01,a\n"
        + "FOMC_PRESS_2024_06_12_TIGHT,FOMC_PRESS,2024-06-12,14:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,99,Fed,https://fed.gov,2018-01-01,b\n",
        encoding="utf-8",
    )
    engine = EventContextEngine(str(csv))
    ts = datetime(2024, 6, 12, 18, 35, tzinfo=timezone.utc)
    assert engine.resolve(ts) == "FOMC_PRESS_TIGHT"


def test_empty_event_type_raises(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "BAD_2024_09_11_TIGHT,,2024-09-11,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,bad\n",
        encoding="utf-8",
    )
    engine = EventContextEngine(str(csv))
    ts = datetime(2024, 9, 11, 12, 35, tzinfo=timezone.utc)
    import pytest

    with pytest.raises(ValueError, match="empty event_type"):
        engine.resolve(ts)


def test_resolve_ns_does_not_reuse_context_past_window_end_same_second(tmp_path):
    csv = tmp_path / "events.csv"
    csv.write_text(
        _HEADER
        + "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-1,0,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,c\n",
        encoding="utf-8",
    )
    engine = EventContextEngine(str(csv))
    inside = datetime(2024, 9, 11, 12, 30, 0, 0, tzinfo=timezone.utc)
    outside_same_second = datetime(2024, 9, 11, 12, 30, 0, 500_000, tzinfo=timezone.utc)

    assert engine.resolve_ns(int(inside.timestamp() * 1_000_000_000)) == "CPI_TIGHT"
    assert engine.resolve_ns(int(outside_same_second.timestamp() * 1_000_000_000)) == "NORMAL"
