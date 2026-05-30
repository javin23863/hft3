# Chicago futures hot-memory universe (hft3 market-state layer)

**Authority PDF:** [chicago_futures_hot_memory_a_plus_developer_prompt.pdf](../references/chicago_futures_hot_memory_a_plus_developer_prompt.pdf)

**Runtime config:** [workbench/config/hot_memory_universe.yaml](../../workbench/config/hot_memory_universe.yaml)

**Code:** `workbench/src/data/instrument_registry.py`, `workbench/src/data/hot_memory_manager.py`

This document is the hft3 index for **market-state** HOT/WARM/COLD residency, instrument registry, volatility sensors, and degradation rules. C++ zero-allocation and lock-free IPC remain under [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) (ultra PDF authority).

## Non-negotiable rules

| Rule | hft3 enforcement |
|------|------------------|
| State layer does not block trading | Manager and telemetry emit state only; no `allow_trade=false` |
| VIX/VVIX are index sensors | `instrument_type=index_sensor`; `assert_not_executable()` raises |
| VX legs are CFE futures | Separate from VIX index; optional with `MISSING` default |
| Core rates/equity protected under load | `core_protected_symbols`: ES, NQ, ZT, ZN, SR3 |
| Missing sensor degrades gracefully | `update_feed_status(MISSING)` does not raise; ES/NQ path continues |
| Promotion audit is PIT-safe metadata | Reason codes in diagnostics only; no replay feature injection |

## Phase gap table (PDF → hft3)

| PDF phase | Topic | hft3 anchor | Status |
|-----------|-------|-------------|--------|
| 1 | Instrument registry | `instrument_registry.py`, `hot_memory_universe.yaml` | **implemented** |
| 2 | HOT universe + residency | `hot_memory_manager.py` | **implemented** |
| 3 | WARM promotion | `promote()` / `demote()` + audit | **implemented** |
| 4 | Cross-asset features | `features_engine/` | **not implemented** |
| 5 | Cross-asset graph | — | **not implemented** |
| 6 | Event calendar | `event_catalog.py` | **partial** |
| 7 | Degradation rules | `apply_load_pressure()`, feed status | **implemented** |
| 8 | Roll hardening | `data_system/src/roll_handler.py` | **partial** (registry fields only) |
| 9 | Feature store | — | **not implemented** |
| 10 | HTTP telemetry API | `campaign_runner` diagnostics hook | **partial** (read-only snapshot) |
| 11 | Live feed wiring | CHI404 / Rithmic | **not implemented** (workstation scope) |
| 12 | Graph memory | — | **deferred** |

## Tiers (v1 config)

| Tier | Symbols (minimum set) |
|------|------------------------|
| HOT_EXECUTABLE | ES, NQ, RTY, YM (+ micros), ZT–UB, SR3, ZQ, CL, NG, GC, HG, 6E (+ micro aliases) |
| HOT_SENSOR | VIX, VVIX (index); VX1, VX2 (CFE futures, optional) |
| WARM | RB, HO, SI, 6J, 6B, 6A, 6C, ZC, ZS, ZW, KE, ZL, ZM |

## Campaign integration

Walk-forward campaigns attach read-only `hot_memory_telemetry` to dry-run preview and per-event `diagnostics.json` via [campaign_runner.py](../../workbench/src/run/campaign_runner.py). This does not alter model signals or execution.

## Reviewer citations

Pass B disputes on market-state memory cite `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` + section (see [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md)).

After-action MANIFEST fields: `hot_memory_universe`, `instrument_registry`, `volatility_sensor_layer`, `hot_memory_degradation`.
