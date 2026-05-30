# Release calendars (sourced dates)

Each CSV row is one official macro release anchor. **Do not invent dates in Python** — maintain these files from the cited `source_url`.

Columns: `release_date`, `event_type`, `source`, `source_url`, `timezone`, `release_time`

Build into `events.csv` via:

```bash
python data_system/scripts/build_events_from_calendar.py
```

Window offsets (TIGHT: -30s / +300s) are applied from templates in the builder, not stored here.
