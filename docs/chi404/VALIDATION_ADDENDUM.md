# CHI404 validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md)

**Scope-green (workstation):** `python -m pytest tests/test_chi404_canonical_guardrails.py tests/test_chi404_baseline_spec.py tests/test_chi404_memory_upgrade.py -q`

**PASS claims:** `validate_pass_criteria.py` on **real** log directory on CHI404 — pytest alone is insufficient.

## Known gaps (open)

1. **Workstation cannot claim colo PASS** — validate scripts must run against CHI404-produced logs.
2. **Optional cmdline tokens** — warn-only in criteria; not all memory PDF tokens enforced as hard fail.
