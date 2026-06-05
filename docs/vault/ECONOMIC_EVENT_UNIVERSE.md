# Economic Event Universe

Authoritative macro, monetary, session, prop, energy, housing, labor, Treasury, and snapshot event universe for offline research. `packages/data_system/config/events.csv` is a generated runnable view, not the full universe authority.

## Package

```python
from economic_event_universe import (
    list_upcoming,
    anchor_utc,
    to_user_tz,
    snapshot_offsets,
    generate_snapshot_times,
    DefaultSnapshotProvider,
)
```

Location: `packages/economic_event_universe/`

| Module | Role |
|--------|------|
| `config/event_universe.yaml` | Full A+ catalog: anchors, windows, symbols, sources |
| `config/calendars/sourced/` | Official `SOURCED` release rows eligible for generated runnable campaigns |
| `config/calendars/seed/` | `SEED` scaffolds visible in inventory, never runnable |
| `service.py` | Unified event types, calendar rows, runnable rows, inventory, snapshot lookup |
| `calendar.py` | Query upcoming rows from generated runnable `events.csv` |
| `timezone.py` | UTC anchors + user IANA display (default doc example: `Asia/Phnom_Penh`) |
| `windows.py` | Download windows + L3 snapshot offset presets |
| `holidays.py` | US federal holidays; claims Thu→Wed shift |
| `labels.py` | Central `E_t` label mapping |
| `snapshot.py` | `CrossAssetSnapshotProvider` → `hfc3.events.l3_event_snapshot_tensor` |
| `fetchers/` | Propose-only calendar diffs → `artifacts/calendar_proposals/` |

## Policy

- **`RESEARCH_READY`** types must have `SOURCED` rows and appear in generated `events.csv`.
- **`CATALOG`** types are defined in YAML for metadata/labels; `SEED` scaffolds live under `config/calendars/seed/` until moved or rewritten as `SOURCED`.
- **Dates are never invented in fetchers.** Fetchers emit JSON proposals; humans merge into `config/calendars/sourced/*.csv`.
- **Live CHI404 hot path unchanged** (BLUEPRINT §4). Snapshot builder is offline research only.
- **C++ E_t labels** — generated from the same YAML via `tools/economic_event_universe/generate_event_context_labels.py` → `event_context_labels.generated.hpp` + compiled golden test `hft_event_context_golden`.

## Workflows

### Validate YAML ↔ calendars

```bash
PYTHONPATH=packages python -m economic_event_universe.cli validate
```

### Rebuild events.csv from sourced calendars

```bash
python -m economic_event_universe.cli build-events --dry-run
python -m economic_event_universe.cli build-events
```

### Inventory full universe

```bash
python -m economic_event_universe.cli inventory
```

### Query upcoming (local timezone)

```python
from economic_event_universe import list_upcoming

for ev in list_upcoming("Asia/Phnom_Penh")[:5]:
    print(ev.event_id, ev.anchor_user_tz, ev.source_url)
```

### Cross-asset L3 tensor (offline)

```bash
python scripts/build_event_cross_asset_snapshot.py --event-id CPI_2024_09_11_TIGHT
```

### Fetcher proposals (dry-run default)

```bash
PYTHONPATH=packages python -m economic_event_universe.fetchers.run_all --dry-run
PYTHONPATH=packages python -m economic_event_universe.fetchers.run_all --write
```

## Adding a release

1. Add metadata to `config/event_universe.yaml` (`official_source_url`, `event_context_label`, windows).
2. Add sourced rows to `packages/economic_event_universe/config/calendars/sourced/<agency>.csv`.
3. Run `python -m economic_event_universe.cli build-events` → generated `events.csv`.
4. Run `validate` and `pytest tests/test_economic_event_universe/`.

## Snapshot offsets

Default L3 offsets (seconds): `-300,-60,-30,-10,0,1,5,10,60,300,1800`. Per-event overrides via YAML `snapshot_offsets_sec`.

## Sources

Every calendar row carries `source_url`. Maintainer runs fetchers quarterly and diffs proposals against CSV.
