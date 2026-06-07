# Equities lane validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../../../docs/VALIDATION_HONESTY.md)

**Scope-green:** `python -m pytest tests/test_equities_lane/ -q`

## Known gaps (open)

1. **Quarantined data** — production Databento download not required for CI; fixture backtest only proves plumbing.
2. **Options normalize** — fixture NDJSON path; external options tape not in default verify.

Update when gaps close.
