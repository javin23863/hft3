# HftBacktest Realism Engine Spec

Official HftBacktest-backed realism runner for CME M6 and campaign replay. Replaces retired certification entrypoints (`run_event_universe`, `run_event_replay` as primary gates).

## Stages

| Stage | Purpose | Engine |
|-------|---------|--------|
| 0 | Input validation | None |
| 1 | Minimal official replay | `run_minimal_official_hftbacktest_replay` |
| 2 | Individual strategy replay | `ReplaySession` event-driven |
| 3 | Stress matrix | Independent scenarios |
| 4 | Combined replay | Shared account (finalists only) |
| 5 | Discrepancy comparison | Observation artifacts |

## Fail-closed

Missing or malformed evidence never passes. Accelerated modes carry `accelerated_not_certifying`.

## Authority

- `packages/backtest_pipeline/src/hftbacktest_realism.py`
- `docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md`
