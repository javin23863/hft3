# Rithmic trial validation addendum

Repo-wide: [docs/VALIDATION_HONESTY.md](../../VALIDATION_HONESTY.md)

**Scope-green:** `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py -q`

## Known gaps (open)

1. **Fixture connector** — smoke passes on synthetic capture; not live Wine/R|Trader bridge on CHI404.
2. **Trial quarantine** — must not write into trusted `data/npz/` without explicit approval.

Live validation requires CHI404 artifacts per [README.md](README.md).
