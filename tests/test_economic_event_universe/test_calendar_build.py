import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
    events_csv = tmp_path / "config" / "events.csv"
    events_csv.parent.mkdir(parents=True)
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "MANUAL_SESSION_2024_01_02_MAIN,PROP_REOPEN,2024-01-02,09:30:00,America/New_York,MAIN,-60,600,"
        "MES.v.0,50,manual,https://example.com,2018-01-01,manual session row\n",
        encoding="utf-8",
    )
    cal_dir = tmp_path / "config" / "release_calendars"
    cal_dir.mkdir()
    (cal_dir / "bls_cpi.csv").write_text(
        "event_type,release_date,release_time,timezone,source,source_url\n"
        "CPI,2024-09-11,08:30:00,America/New_York,BLS,https://bls.gov\n",
        encoding="utf-8",
    )

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "data_system_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "MANUAL_SESSION_2024_01_02_MAIN" in text
    assert "CPI_2024_09_11_TIGHT" in text


def test_builder_strips_seed_rows(tmp_path, monkeypatch):
    events_csv = tmp_path / "config" / "events.csv"
    events_csv.parent.mkdir(parents=True)
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "CPI_2020_01_01_TIGHT,CPI,2020-01-01,08:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10,BLS,https://bls.gov,2018-01-01,SEED_PLACEHOLDER: replace with sourced agency date before research use\n",
        encoding="utf-8",
    )
    cal_dir = tmp_path / "config" / "release_calendars"
    cal_dir.mkdir()

    import data_system.scripts.build_events_from_calendar as builder

    monkeypatch.setattr(builder, "data_system_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_events_from_calendar.py"])
    builder.main()

    text = events_csv.read_text(encoding="utf-8")
    assert "CPI_2020_01_01_TIGHT" not in text
