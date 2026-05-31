"""Overlapping window tie-break uses context_priority from YAML (not CSV priority)."""

from datetime import datetime, timezone

from features_engine.src.regime.event_context import EventContextEngine


def test_overlap_fomc_press_wins(tmp_path, monkeypatch):
    csv = tmp_path / "events.csv"
    csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "FOMC_STATEMENT_2024_06_12_TIGHT,FOMC_STATEMENT,2024-06-12,14:00:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10, Fed,https://fed.gov,2018-01-01,a\n"
        "FOMC_PRESS_2024_06_12_TIGHT,FOMC_PRESS,2024-06-12,14:30:00,America/New_York,TIGHT,-60,600,"
        "MES.v.0,10, Fed,https://fed.gov,2018-01-01,b\n",
        encoding="utf-8",
    )
    engine = EventContextEngine(str(csv))
    # 14:35 ET = 18:35 UTC during EDT
    ts = datetime(2024, 6, 12, 18, 35, tzinfo=timezone.utc)
    assert engine.resolve(ts) == "FOMC_PRESS_TIGHT"
