import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _layout_repo(tmp_path: Path) -> Path:
    """Minimal repo tree: packages/data_system/config + release_calendars."""
    ds = tmp_path / "packages" / "data_system"
    (ds / "config" / "release_calendars").mkdir(parents=True)
    return tmp_path


def test_builder_dry_run_reports_many_types():
    proc = subprocess.run(
        [sys.executable, str(REPO / "packages" / "data_system" / "scripts" / "build_events_from_calendar.py"), "--dry-run"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "dry-run:" in proc.stdout
    assert "CPI:" in proc.stdout
    assert "NFP:" in proc.stdout
    assert "PROP_FLATTEN_TOPSTEP:" in proc.stdout


def test_builder_preserves_manual_rows(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    events_csv = ds / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "MANUAL_SESSION_2024_01_02_MAIN,PROP_REOPEN,2024-01-02,09:30:00,America/New_York,MAIN,-60,600,"
        "MES.v.0,50,manual,https://example.com,2018-01-01,manual session row\n",
        encoding="utf-8",
    )
    cal_dir = ds / "config" / "release_calendars"
    (cal_dir / "bls_cpi.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "CPI,2024-09-11,08:30:00,America/New_York,BLS,https://bls.gov\n",
        encoding="utf-8",
    )

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "repo_root", lambda: repo)
    monkeypatch.setattr(builder, "data_system_root", lambda: ds)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "MANUAL_SESSION_2024_01_02_MAIN" in text
    assert "CPI_2024_09_11_TIGHT" in text


def test_builder_strips_seed_rows(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    events_csv = ds / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "CPI_2020_01_01_TIGHT,CPI,2020-01-01,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,SEED_PLACEHOLDER: replace with sourced agency date before research use\n",
        encoding="utf-8",
    )

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "repo_root", lambda: repo)
    monkeypatch.setattr(builder, "data_system_root", lambda: ds)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "CPI_2020_01_01_TIGHT" not in text


def test_builder_includes_catalog_type_without_filename_allowlist(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    events_csv = ds / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n",
        encoding="utf-8",
    )
    cal_dir = ds / "config" / "release_calendars"
    (cal_dir / "bea_retail_sales.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "RETAIL_SALES,2024-06-18,08:30:00,America/New_York,Census,https://www.census.gov/retail/\n",
        encoding="utf-8",
    )

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "repo_root", lambda: repo)
    monkeypatch.setattr(builder, "data_system_root", lambda: ds)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py", "--start-year", "2024", "--end-year", "2024"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "RETAIL_SALES_2024_06_18_TIGHT" in text
    assert "Census" in text


def test_builder_preserves_csv_release_time_over_yaml(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    events_csv = ds / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n",
        encoding="utf-8",
    )
    cal_dir = ds / "config" / "release_calendars"
    (cal_dir / "bls_cpi.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "CPI,2024-09-11,10:00:00,America/New_York,BLS,https://bls.gov/cpi\n",
        encoding="utf-8",
    )

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "repo_root", lambda: repo)
    monkeypatch.setattr(builder, "data_system_root", lambda: ds)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py", "--start-year", "2024", "--end-year", "2024"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "CPI_2024_09_11_TIGHT" in text
    assert ",10:00:00," in text
    assert "https://bls.gov/cpi" in text


def test_manual_row_detection_not_triggered_by_manually_word(tmp_path):
    from economic_event_universe.events_csv_builder import is_manual_events_csv_row

    assert not is_manual_events_csv_row({"notes": "manually curated CPI override", "source": "BLS"})
    assert is_manual_events_csv_row({"notes": "manual session row", "source": "BLS"})


def test_partial_year_override(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    cal_dir = ds / "config" / "release_calendars"
    (cal_dir / "bls_cpi.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "CPI,2019-02-13,08:30:00,America/New_York,BLS,https://bls.gov\n"
        "CPI,2024-09-11,08:30:00,America/New_York,BLS,https://bls.gov\n",
        encoding="utf-8",
    )

    from economic_event_universe.events_csv_builder import iter_events_csv_rows
    import economic_event_universe.walk_forward_years as wf

    monkeypatch.setattr(wf, "backtest_year_range", lambda _root: (2018, 2025))
    rows = iter_events_csv_rows(repo, include_rule_based=False, start_year=2024, end_year=None)
    assert len(rows) == 1
    assert rows[0]["release_date"] == "2024-09-11"


def test_backtest_scope_includes_new_calendar_before_events_csv_rebuild(tmp_path, monkeypatch):
    repo = _layout_repo(tmp_path)
    ds = repo / "packages" / "data_system"
    (ds / "config" / "events.csv").write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n",
        encoding="utf-8",
    )
    cal_dir = ds / "config" / "release_calendars"
    (cal_dir / "bea_retail_sales.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "RETAIL_SALES,2024-06-18,08:30:00,America/New_York,Census,https://www.census.gov/retail/\n",
        encoding="utf-8",
    )

    from economic_event_universe.events_csv_builder import iter_backtest_scope_windows

    windows = iter_backtest_scope_windows(repo, start_year=2024, end_year=2024)
    assert len(windows) == 1
    assert windows[0].event_type == "RETAIL_SALES"


def test_walk_forward_year_range_matches_workbench_config():
    from economic_event_universe.walk_forward_years import backtest_year_range

    start, end = backtest_year_range(REPO)
    assert start == 2018
    assert end == 2025


def test_backtest_scope_windows_match_sourced_calendars():
    from economic_event_universe.events_csv_builder import iter_backtest_scope_windows, iter_sourced_events_csv_rows

    windows = iter_backtest_scope_windows(REPO)
    rows = iter_sourced_events_csv_rows(REPO)
    assert len(windows) == len(rows)
    assert {w.event_id for w in windows} == {r["event_id"] for r in rows}
