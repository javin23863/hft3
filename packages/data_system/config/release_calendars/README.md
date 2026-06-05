# Release calendars moved

This directory is a compatibility pointer only. Calendar ownership now lives in:

- `packages/economic_event_universe/config/calendars/sourced/`
- `packages/economic_event_universe/config/calendars/seed/`

Each CSV row must carry explicit `row_status`:

- `SOURCED` — official agency date eligible for generated `events.csv`
- `SEED` — visible in the universe inventory but not runnable
- `DISABLED` / `RETIRED` — retained metadata, not runnable

Generated runnable view:

```bash
python -m economic_event_universe.cli build-events --dry-run
python -m economic_event_universe.cli build-events
```

Legacy wrapper retained for old commands:

```bash
python packages/data_system/scripts/build_events_from_calendar.py --dry-run
```

Window offsets (TIGHT: -30s / +300s) are applied from templates in the builder, not stored here.
