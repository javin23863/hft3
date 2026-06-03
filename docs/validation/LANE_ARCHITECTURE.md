# Lane Architecture (hft3 backtester validation)

**Status:** Phase 38 design plan implemented (phases 1-5); phase 39 test suite green (62 tests); phase 41 lane-aware backtester certification integrated (`run_full_certification` runs all 4 lanes).

## Purpose

The hft3 backtester validation system was originally CME-futures-only
(`packages/hft3/validation/certification_runner.py`,
`promotion_gate.py`). The lane-aware unification makes certification and
promotion **lane-agnostic**: CME, crypto, equities, and options lanes
each register as first-class citizens with their own latency bands,
horizons, windows, tick sizes, lot sizes, test paths, and event types.

The legacy CME-specific entry points remain functional for backward
compatibility; new code should use the lane-aware API.

## Lane identity

A `Lane` is one of: `CME_FUTURES`, `CRYPTO`, `EQUITIES`, `OPTIONS`.

Resolution priority for a candidate (model + symbol + event):

1. `model_id` prefix → registered lane (e.g. `CRYPTO_H7` → `CRYPTO`)
2. `symbol` prefix → BTC/ETH/SOL → `CRYPTO`; RUNNER/LOW_FLOAT → `EQUITIES`; OPTIONS/PARITY → `OPTIONS`
3. `event_id` prefix → `CRYPTO_`/`BTC_`/`ETH_`/`SOL_` → `CRYPTO`; etc.
4. Default: `CME_FUTURES`

Implemented in `resolve_lane_for_candidate()` at
`packages/hft3/validation/lanes/lane_aware_promotion.py:43`.

## Core abstractions

- `Lane` (StrEnum): identity.
- `WindowConfig` / `HorizonConfig`: per-lane windowing params.
- `LaneConfig` (Protocol): per-lane static config (symbols, event_types,
  latency_bands_ms, tick_size, lot_size, windows, horizons, …).
- `Backtester` (Protocol): `validate_config() -> list[str]`,
  `run(target) -> BacktestResult`.
- `BacktestResult` (Protocol): per-run outcome.
- `GenericBacktestResult`: default `BacktestResult` implementation
  (lane, target, passed, returncode, degraded, metrics).
- `LaneRegistration`: per-lane entry in the registry.
- `LaneRegistry`: singleton; `register_lane()`, `get()`,
  `all_lanes()`, `resolve_lane()`.

Located in `packages/hft3/validation/lanes/`.

## Per-lane adapters

Each lane ships an adapter in
`packages/hft3/validation/lanes/adapters/`:

- `cme_adapter.py`: `CMEConfig`, `load_cme_config()`, `CMEBacktester`.
  Loads `packages/data_system/config/events.csv` by default; falls back
  to built-in CME defaults. Latency bands `[0.5, 1, 2, 5, 10] ms`.
- `crypto_adapter.py`: `CryptoConfig`, `load_crypto_config()`,
  `CryptoBacktester`. Reads candidate YAML; parses `90d`/`30d`/`24h`
  durations; defaults to BTC/ETH/SOL spot+perp. Latency bands
  `[5, 50, 200] ms`. 24h embargo; 90/30-day walk-forward.
- `equities_adapter.py`: `EquitiesConfig`, `load_equities_config()`,
  `EquitiesBacktester`. Reads `universe.yaml`; extracts session symbols
  + walk-forward config. Latency bands `[5, 50] ms` default.
- `options_adapter.py`: `OptionsConfig`, `load_options_config()`,
  `OptionsBacktester`. Defaults; lot size 100, tick 0.01. Latency
  1 ms.

`registration.py` calls `register_all_lanes()` to auto-register all
four on first import.

## Scorecard

`build_lane_scorecard()` produces a `LaneScorecard` keyed by lane,
with per-lane `LaneCoverage` (symbols, event_types, latency_bands_ms,
test_paths, run_result). `legacy_cme_scorecard_fields()` extracts the
old `covered_symbols` / `covered_event_types` / etc. fields for
backward compatibility with code that reads the legacy CME scorecard.

## Unified certification runner

`run_unified_certification(lanes=None, skip_pytest=False, pytest_timeout=60.0)` discovers
all registered lanes and runs each lane's test paths under pytest. The
resulting `LaneScorecard` includes a `run_result` per lane
(`passed`, `returncode`, `output_tail`, `test_paths`,
`failure_notes`).

`write_unified_certification_report()` persists the scorecard to
`runtime/validation/lane_certification_report.json`.

`main()` provides a CLI:
```
python -m hft3.validation.lanes.unified_certification_runner \
    --lanes crypto equities --skip-pytest
```

## Lane-aware backtester certification (Phase 41)

The T2 backtester certification tier (`run_full_certification` in
`packages/hft3/validation/certification_runner.py`) now runs the
lane-aware unified runner for **all 4 lanes** in addition to the
legacy T0 fast gate and T2 full suite:

- **T0 fast gate** — `tests/backtester_validation/fast` (CME core, fast).
- **T2 full suite** — `tests/backtester_validation/full` (CME core, adversarial).
- **Crypto lane** — `tests/test_crypto_lane` (137 tests).
- **Equities lane** — `tests/test_equities_lane` (43 tests).
- **Options lane** — `tests/test_options_lane` (currently no test dir; marked as PASS when skipped).

CME core is not duplicated — `run_full_certification` calls
`run_unified_certification(lanes=[CRYPTO, EQUITIES, OPTIONS])` to
avoid running the backtester_validation/{fast,full} tests twice. CME
is recorded in `lane_results["cme_futures"]` with `covered_by: "T0
fast gate + T2 full suite (CME core)"`.

### Status decision

| Condition | Status |
|-----------|--------|
| T0 fast gate fails | **RED** |
| T2 full suite fails (>2 tests) | **RED** |
| T2 full suite fails (≤2 tests) | **YELLOW** |
| Any non-CME lane pytest fails | **YELLOW** (warning, not blocking) |
| All pass | **GREEN** |

### Coverage aggregation

`covered_symbols`, `covered_event_types`, `covered_latency_bands_ms`,
and `covered_modules` in the scorecard are the **union across all 4
lanes**, not just CME:

- 18 symbols (12 CME + 5 crypto + 1 equities)
- 8 event types (2 CME + 3 crypto + 2 equities + 1 options)
- 7 latency bands (5 CME + 2 crypto)
- 8 modules (5 CME + crypto_lane + equities_lane + options_lane)

### Artifacts

- `runtime/validation/backtester_certification_scorecard.json` — full
  scorecard with `lane_results`, `lane_coverage`, `legacy_cme_fields`.
- `runtime/validation/backtester_certification_scorecard.md` — human-readable.
- `runtime/validation/certification_registry.json` + `.jsonl` — registry with status + git SHA + union coverage.
- `runtime/validation/lane_certification_report.json` — separate lane-only report (written by `write_unified_certification_report`).

### Skipping lane pytest

For CI scenarios where you only want the CME core (T0/T2) and not the
lane tests (which take ~30s extra), pass `skip_lane_pytest=True`:

```python
from hft3.validation.certification_runner import run_full_certification
result = run_full_certification(skip_lane_pytest=True)
```

## Per-lane staleness

`LaneStalenessPaths` enumerates the per-lane critical paths:

- CME: `packages/hft3/validation/`, `scripts/run_event_replay.py`,
  `packages/data_system/config/events.csv`.
- Crypto: `packages/crypto_lane/`, `packages/crypto_l2_adapter/`,
  `tests/test_crypto_lane/`.
- Equities: `packages/equities_lane/`, `tests/test_equities_lane/`.
- Options: `packages/options_lane/`, `tests/test_options_lane/`.

`all_critical_paths()` returns the union. A change to any of these
paths invalidates the lane-aware GREEN certification.

## Lane-aware promotion

`check_candidate_lane_coverage(model_id, symbol, event_id, latency_ms)`
resolves the candidate's lane and checks whether the symbol, event
type, and latency band are covered by that lane's scorecard.

- **Passes** when symbol matches a coverage symbol, event_id contains
  a registered event_type, and latency_ms is within a registered band.
- **Fails** for cross-lane mismatches (e.g. crypto symbol against CME
  coverage).
- **Rejects** unknown symbols (must be on the lane's coverage list).

## Test surface

`tests/test_hft3_validation/` (62 tests, phase 39):

- `test_lane_registry.py` (9): enum, prefix resolution, registration.
- `test_lane_adapters.py` (18): Protocol satisfaction, defaults, YAML/CSV loading, validation.
- `test_unified_scorecard.py` (8): per-lane coverage, legacy CME fields.
- `test_unified_certification_runner.py` (11): full + subset runs, report, staleness.
- `test_lane_aware_promotion.py` (18): lane resolution, cross-lane rejection, latency bands.

Run with: `pytest tests/test_hft3_validation/ -v`.

## See also

- [LANE_UNIFICATION_PLAN.md](./LANE_UNIFICATION_PLAN.md): design rationale and rollout.
- [MIGRATION_GUIDE.md](./MIGRATION_GUIDE.md): how to add a new lane or migrate existing code.
