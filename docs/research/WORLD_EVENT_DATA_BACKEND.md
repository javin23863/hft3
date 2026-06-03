# World Event Data Backend

Backend-only world-event data source for slow relationship reasoning. This is not a UI feature, not a trading input, and not an OpenFoundry/KG write path.

## Source

| Source | hft3 implementation | Cache path | Authority |
|--------|---------------------|------------|-----------|
| GDELT 2.1 Events | `packages/research_pipeline/world_events/` | `artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl` | Offline cached event records with actor, event code, location, tone, source URL, request URL, and fetch timestamp provenance |

Canonical relationship evidence ref:

```text
artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl:<GLOBALEVENTID>
```

Example:

```text
artifacts/world_events/gdelt/events/20240601.jsonl:123456789
```

## Boundary

- No FinceptTerminal code is copied, vendored, imported, or linked.
- FinceptTerminal was reviewed only as a public reference for connector categories; its AGPL/commercial licensing is not pulled into hft3 by this backend.
- The connector is hft3-authored stdlib Python.
- Imports perform no network fetches.
- Fetching is explicit through `GdeltWorldEventConnector.fetch(...)` with a bounded timeout.
- Cache writes go under `artifacts/world_events/gdelt/`, not `data/npz/`, not live paths, and not `research_cards/kg/`.
- Relationship promotion records remain review artifacts only.

## Relationship Reasoning Use

`RelationshipDataSource.GDELT_WORLD_EVENTS` is valid only for `RelationshipContext.WORLD_EVENT`.

The relationship validator requires:

1. source type `gdelt_world_events`
2. canonical source ref `artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl:<GLOBALEVENTID>`
3. an existing cache file under the supplied repo root
4. a cached record whose `event_id`, `event_date`, provider, provider version, and `source_url` provenance match the ref
5. evidence description
6. proof trace
7. non-authoritative/offline-only/no-write flags

The GDELT source is empirical, so a validated `world_event` relationship candidate can satisfy the "at least one empirical offline source" rule. It still cannot become authoritative without a separate reviewed promotion path.
