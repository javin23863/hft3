# Lane-Aware Backtester Validation — Design Plan (Option C: Full Unification)

> Status: approved, build mode active.
> Scope: make the backtester validation system lane-agnostic. All four lanes (CME, crypto, equities, options) share a single `Backtester` Protocol, a single `LaneConfig` schema, and a single unified certification runner.
> Constraint: adapters only — no edits to teammate-owned `packages/crypto_lane/`, `packages/equities_lane/`, or `packages/options_lane/`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  LaneConfig Protocol                                        │
│  ├─ lane: Lane enum                                         │
│  ├─ symbols: list[str]                                      │
│  ├─ windows: WindowConfig                                   │
│  ├─ horizons: HorizonConfig                                 │
│  ├─ latency_bands_ms: list[float]                           │
│  └─ tick_size / lot_size (lane-specific)                    │
└─────────────────────────────────────────────────────────────┘
            ↑                ↑               ↑
     CMEConfig      CryptoConfig    EquitiesConfig   OptionsConfig
            │                │               │
            └────────────────┴───────────────┘
                             │
              ┌──────────────┴──────────────┐
              │  Backtester Protocol         │
              │  run(config) -> Result       │
              └──────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ReplaySession       CryptoReplay         LowFloatBT       MultiLegBT
   (CME)               (crypto)             (equities)       (options)
                             │
              ┌──────────────┴──────────────┐
              │  LaneRegistry                │
              │  model_id prefix → lane      │
              │  lane → validator + config   │
              └──────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │  Unified Certification       │
              │  Runner                      │
              └──────────────────────────────┘
```

---

## Phase 1: Core Abstractions (foundation)

**Files to create in `packages/hft3/validation/lanes/`:**

- `__init__.py` — re-exports
- `lane.py` — `Lane` enum, `WindowConfig`, `HorizonConfig`, `LaneConfig` Protocol, `BacktestResult` Protocol
- `backtester_protocol.py` — `Backtester` Protocol, `validate_config()` helper
- `lane_registry.py` — `LaneRegistry` singleton: `model_id` prefix → `Lane`, each lane registers its adapter, config loader, validator, test path

Key design decisions:
- `Lane` is a `StrEnum` for JSON serialization
- `LaneConfig` is a `Protocol` (structural typing, no inheritance required)
- Adapters register via a `@register_lane` decorator at import time
- The registry is the single source of truth — no `asset_class` string sniffing

---

## Phase 2: Per-Lane Adapters (wrappers, not rewrites)

**Files in `packages/hft3/validation/lanes/adapters/`:**

- `cme_adapter.py` — wraps `ReplaySession`/`signal_backtester`, loads `events.csv`
- `crypto_adapter.py` — wraps `crypto_execution_validator`, loads per-candidate YAML
- `equities_adapter.py` — wraps `LowFloatBacktester`, loads `universe.yaml`
- `options_adapter.py` — wraps `MultiLegParityBacktester`, minimal config

Each adapter:
- Implements `Backtester` Protocol by delegating to the native lane code
- Exposes `to_lane_config()` to produce a unified `LaneConfig` from the lane's native config
- Does **not** modify the lane's own code

---

## Phase 3: Lane-Aware Scorecard

**Files in `packages/hft3/validation/`:**

- `scorecard.py` — `LaneScorecard` dataclass with per-lane coverage sections
- Update `certification_registry.py` — add `lane_coverage` field (additive, backward-compatible)
- Keep legacy `covered_symbols`/`covered_event_types` for CME

Scorecard structure:
```python
{
  "covered_lanes": ["cme_futures", "crypto", "equities", "options"],
  "lane_coverage": {
    "cme_futures": {"symbols": [...], "event_types": [...], "latency_bands_ms": [...], ...},
    "crypto":     {"symbols": [...], "event_types": [...], "latency_bands_ms": [...], ...},
    "equities":   {"symbols": [...], "event_types": [...], "latency_bands_ms": [...], ...},
    "options":    {"symbols": [...], "event_types": [...], "latency_bands_ms": [...], ...},
  }
}
```

---

## Phase 4: Unified Certification Runner + Staleness

**Files in `packages/hft3/validation/`:**

- `unified_certification_runner.py` — discovers lanes via `LaneRegistry`, runs each lane's validation, aggregates
- `unified_staleness.py` — each lane registers its own critical paths
- Keep `certification_runner.py` and `core_engine_paths.py` as CME-specific (backward-compatible)

---

## Phase 5: Lane-Aware Promotion Gate

**File: `packages/hft3/validation/promotion_gate.py`**

- Update `_scorecard_covers()` to take a `lane` parameter
- Look up the lane's coverage in `scorecard["lane_coverage"]`
- Fall back to legacy CME check if `lane` is not provided
- Cross-lane promotion (e.g., crypto symbol against CME coverage) is rejected

---

## Phase 6: Test Suite

**Files in `tests/test_hft3_validation/`:**

- `test_lane_registry.py` — prefix → lane resolution, validator registration
- `test_lane_adapters.py` — each adapter produces a valid `LaneConfig`
- `test_unified_scorecard.py` — per-lane coverage fields, backward compatibility
- `test_unified_certification_runner.py` — discovers and runs all lanes
- `test_lane_aware_promotion_gate.py` — crypto/equities candidates now pass when lane matches

---

## Phase 7: Documentation

**Files in `docs/validation/`:**

- `LANE_ARCHITECTURE.md` — Lane Protocol overview, how to add a new lane
- `MIGRATION_GUIDE.md` — backward compatibility, migration path for existing CME consumers

---

## Risk & Constraint Analysis

| Risk | Mitigation |
|------|------------|
| Teammate-owned files (crypto, equities, options lanes) | Adapters are wrappers in `hft3/validation/lanes/adapters/` — no edits to lane internals |
| Backward compatibility for CME-only consumers | Legacy `covered_symbols`/`covered_event_types` fields kept; `run_full_certification()` signature unchanged |
| Latency band semantics differ across lanes | Each lane declares its own `latency_bands_ms`; promotion gate checks the candidate's lane, not a global list |
| Walk-forward shapes differ | Each lane's adapter maps its native shape to unified `WindowConfig`/`HorizonConfig` |
| Tick size / lot size differ | `LaneConfig.tick_size` and `lot_size` are lane-specific, loaded from each lane's config |
| Options lane has minimal configuration | Adapter uses `latency_ms=1.0` as the only knob; `horizons` is empty list |

---

## Implementation Order

1. Phase 1: Core abstractions (Lane, LaneConfig, Backtester Protocol, LaneRegistry)
2. Phase 2: Per-lane adapters (four lane adapters — independent of each other)
3. Phase 3: Lane-aware scorecard
4. Phase 4: Unified certification runner + staleness
5. Phase 5: Lane-aware promotion gate
6. Phase 6: Test suite (can be written incrementally)
7. Phase 7: Documentation

---

## Success Criteria

- All existing 352 tests still pass
- New tests verify: crypto candidate with `BTCUSDT` symbol can be promoted when crypto lane coverage includes it
- New tests verify: equities candidate with `RUNNER` symbol can be promoted when equities lane coverage includes it
- New tests verify: cross-lane promotion is rejected
- Scorecard includes `lane_coverage` field with all four lanes
- `LaneRegistry` resolves all known `model_id` prefixes
- Each adapter produces a valid `LaneConfig` from its lane's native config
