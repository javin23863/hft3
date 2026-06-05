# Workbench validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../../../docs/VALIDATION_HONESTY.md)

**Scope-green:** `python -m pytest tests/test_workbench/ -q`

## Known gaps (open)

1. **Python wall time** — not production latency authority; CHI404 C++ distributions required for promotion claims.
2. **Quote-engine hybrid** — aggregate-only AAR waiver; not per-trade `trades.parquet` audit.
3. **Catalog-event e2e** — excluded from default agent verify budget; full e2e is separate gate.

Update when gaps close.
