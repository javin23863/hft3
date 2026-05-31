# Release calendars (sourced dates)

Each CSV row is one official macro release anchor. **Do not invent dates in Python** — maintain these files from the cited `source_url`.

Columns: `release_date`, `event_type`, `source`, `source_url`, `timezone`, `release_time`, `row_status`

- `SOURCED` — official agency date (eligible for default `events.csv` build)
- `SEED` — scaffolding only; skipped unless builder `--include-seed`

Build into `events.csv` via:

```bash
python packages/data_system/scripts/build_events_from_calendar.py
```

Regenerate C++ E_t label table after YAML edits:

```bash
python tools/economic_event_universe/generate_event_context_labels.py
```

Window offsets (TIGHT: -30s / +300s) are applied from templates in the builder, not stored here.
