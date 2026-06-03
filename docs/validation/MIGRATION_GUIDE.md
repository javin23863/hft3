# Migration Guide: CME-Only → Lane-Aware Validation

This guide explains how to add a new lane and how to migrate
existing CME-only validation code to the lane-aware API.

## Why migrate?

Before phase 38, the certification runner and promotion gate were
hard-coded to CME (`packages/hft3/validation/certification_runner.py`,
`promotion_gate.py:check_lane_coverage` was a CME-only check). Crypto,
equities, and options candidates were either out-of-scope or had to
fake their way in by reusing CME coverage.

Phase 38 fixes this. Existing code keeps working (backward compat);
new code should use the lane-aware API.

## Adding a new lane

1. **Define the lane identity.** Add an entry to the `Lane` enum in
   `packages/hft3/validation/lanes/lane.py`:

   ```python
   class Lane(str, Enum):
       ...
       FX = "fx"  # new lane
   ```

2. **Create the adapter.** New file
   `packages/hft3/validation/lanes/adapters/fx_adapter.py` exposing:

   - `FXConfig` dataclass implementing the `LaneConfig` Protocol
     (lane, symbols, event_types, latency_bands_ms, tick_size,
     lot_size, windows, horizons, ...).
   - `load_fx_config(...) -> FXConfig`.
   - `FXBacktester(FXConfig)` implementing the `Backtester` Protocol
     (`validate_config() -> list[str]`, `run(target) -> BacktestResult`).
   - Add `to_dict()` on `FXConfig` for the scorecard round-trip.

3. **Register the lane.** Add a register call to
   `packages/hft3/validation/lanes/registration.py`:

   ```python
   def register_fx_lane(reg: LaneRegistry) -> None:
       reg.register_lane(
           Lane.FX,
           LaneRegistration(
               lane=Lane.FX,
               config_class=FXConfig,
               test_paths=("tests/test_fx_lane",),
               ...
           ),
       )

   def register_all_lanes() -> None:
       ...
       register_fx_lane(LaneRegistry.instance())
   ```

4. **Add staleness paths.** Extend
   `packages/hft3/validation/lanes/unified_staleness.py` to include
   the new lane's critical paths under `get_lane_staleness_paths()`.

5. **Add lane resolution rules.** In
   `lane_aware_promotion.py:resolve_lane_for_candidate()`, add
   prefix checks for the new lane (model_id, symbol, event_id).

6. **Write tests.** Add a `test_fx_adapter.py` to
   `tests/test_hft3_validation/` covering: config defaults, YAML
   loading, Protocol satisfaction, coverage check, cross-lane
   rejection.

7. **Update docs.** Add a lane row to
   `docs/validation/LANE_ARCHITECTURE.md`.

## Migrating existing code

### Reading coverage

**Before (CME-only):**

```python
covered = scorecard["covered_symbols"]  # legacy field
```

**After (lane-aware):**

```python
from hft3.validation.lanes import build_lane_scorecard, legacy_cme_scorecard_fields

card = build_lane_scorecard()
crypto_coverage = card.lane_coverage["crypto"]  # per-lane
legacy = legacy_cme_scorecard_fields(card)  # backward compat
cme_symbols = legacy["covered_symbols"]
```

### Checking promotion eligibility

**Before (CME-only):**

```python
# promotion_gate.py:_scorecard_covers
if symbol in scorecard["covered_symbols"] and ...
```

**After (lane-aware):**

```python
from hft3.validation.lanes.lane_aware_promotion import check_candidate_lane_coverage

result = check_candidate_lane_coverage(
    model_id=model_id,
    symbol=symbol,
    event_id=event_id,
    latency_ms=latency_ms,
)
if not result.passed:
    raise PromotionFailed(result.failure_reasons)
```

The new API resolves the lane and checks coverage within it. Cross-lane
mismatches (e.g. crypto symbol against CME coverage) are rejected.

### Running certification

**Before (CME-only):**

```python
from hft3.validation.certification_runner import run_certification
report = run_certification()
```

**After (lane-aware):**

```python
from hft3.validation.lanes.unified_certification_runner import (
    run_unified_certification,
    write_unified_certification_report,
)

card = run_unified_certification(skip_pytest=False)
write_unified_certification_report(card)
# per-lane results available in card.lane_coverage[lane]["run_result"]
```

### Staleness

**Before:**

```python
from hft3.validation.certification_staleness import all_critical_paths
paths = all_critical_paths()  # CME-only
```

**After:**

```python
from hft3.validation.lanes.unified_staleness import (
    get_lane_staleness_paths,
    all_critical_paths,
)
paths = get_lane_staleness_paths()  # per-lane
all_paths = all_critical_paths()    # union
```

## Backward compatibility

The legacy CME-specific entry points are preserved:

- `packages/hft3/validation/certification_runner.py` — still works.
- `promotion_gate.py:_scorecard_covers` — still CME-only but now
  falls back to lane-aware check when CME coverage is missing.
- `covered_symbols` / `covered_event_types` fields on the scorecard —
  still populated for CME via `legacy_cme_scorecard_fields()`.

New code should use the lane-aware API. Legacy code will continue to
work without modification.

## Test compatibility

Existing tests that exercise the legacy CME certification path
(`tests/test_backtester_validation/`, `tests/test_phase25_*`) are
unaffected. The new `tests/test_hft3_validation/` covers the lane-aware
API only.
