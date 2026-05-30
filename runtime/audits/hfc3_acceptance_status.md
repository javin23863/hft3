# HFC3 Phase 10 — acceptance status

Honest status for cross-asset L3 infrastructure (not production-ready research).

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Repo audit identifies CPI/ES/single-instrument assumptions | **PASS** | `runtime/audits/hfc3_l3_cross_asset_repo_audit.md` |
| 2 | Event snapshots from L3 MBO, not L1/L2 | **PASS** | `hfc3/events/l3_event_snapshot_tensor.py` — `data_source=MBO_DERIVED` |
| 3 | ES not only response instrument | **PARTIAL** | Targets schema supports multi-instrument; NPZ on disk is mostly MES/ES |
| 4 | CPI not only event type | **FAIL** | `events.csv` still CPI/NFP/PROP only; registry YAML is not populated rows |
| 5 | Multi-asset state at event timestamp | **PARTIAL** | Tensor builder is multi-symbol; needs NPZ per instrument |
| 6 | Tradable futures use MBO canonical | **PASS** | No L1/L2 download path; `MBO_MISSING` explicit in inventory |
| 7 | VIX/VVIX as sensors not MBO | **PASS** | `SENSOR_ONLY` in inventory; optional `sensor_df` hook |
| 8 | Missing MBO reported explicitly | **PASS** | `runtime/data_audits/hfc3_missing_mbo_data_jobs.json` |
| 9 | Cross-asset groups ablation-tested | **PARTIAL** | Harness + OLS R² metric exist; not run across full catalog yet |
| 10 | Filtration integrity | **PARTIAL** | Tensor/targets OK; cross-asset ordinal now capped at `offset_sec`; RegimeFilter cumulative state TBD |
| 11 | Replay-safe event windows | **PASS** | Point-in-time windows from `events.csv` |
| 12 | CHI404 latency bands 0.5/1/2 ms | **PASS** | `backtest_pipeline/src/runner.py` + replay wrapper |
| 13 | Ablation shows help/hurt/noise | **PARTIAL** | Verdict logic on OLS R²; needs multi-symbol NPZ + catalog sweep |
| 14 | No hard-coded alpha | **PASS** | Infrastructure only |
| 15 | Model layer free to discover value | **PASS** | Features exported; no strategy embedded |
| 16 | Cross-asset replay feeds backtest features | **FAIL** | `multi_asset_replay.py` runs single-NPZ path; cross-asset tensor is research artifact |

## Blockers before claiming Phase 8/9 PASS

1. Backfill MBO NPZ for NQ/ZN/GC/CL/6E per `hfc3_missing_mbo_data_jobs.md`.
2. Add sourced FOMC/EIA/USDA rows to `events.csv` (no synthetic surprise fields).
3. Run ablation across CPI/NFP catalog with ≥2 instruments per group.
4. Wire cross-asset tensor into replay runner or document as offline-only (currently offline-only).
