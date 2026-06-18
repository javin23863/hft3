# HftBacktest Campaign Cache Specification

## Worker immutable caches

- Prepared data metadata (path, hash)
- Feature timeline paths
- Validated latency/queue JSON

## Limits

`HftCampaignConfig`: `prepared_data_cache_entries`, `feature_timeline_cache_entries`, `max_scenarios_per_worker`

## Invalidation

Prepared data: hash key change or missing `.complete` marker.
Scenario cache: fail-closed revalidation of hashes, schema, provenance on resume.

## Metrics

Campaign summary reports cache hits, misses, evictions, reuse counts.
