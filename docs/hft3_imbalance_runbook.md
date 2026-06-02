# HFT3 imbalance runbook

## Data sources

| Asset class | Primary schema | Book imbalance | True OFI | Auction imbalance |
|-------------|----------------|----------------|----------|-------------------|
| Futures (GLBX) | `mbo` NPZ | full | full | unavailable (macro labels only) |
| Equities | `mbo` / fallback `mbp-10` | full / MBP path | full / proxy | `imbalance` schema via `databento_auction_imbalance.py` |
| Options parity | `mbo` futures legs; `mbp-1` options | proxy on options | trade-pressure on quotes | unavailable |

Inventory: [hft3_imbalance_inventory.md](hft3_imbalance_inventory.md) (regenerate with `python scripts/build_imbalance_inventory.py`).

## Labels

- **order_flow_imbalance** — MBO event sequencing (ADD/CANCEL/TRADE).
- **order_flow_imbalance_proxy** — MBP-10 depth deltas only.
- **trade_pressure_only** — aggressor volume when book sequencing insufficient.
- **auction_imbalance** — venue auction feed only; never merged into continuous book features by default.

MBP-10 is **aggregated depth, not Level 3**.

## Commands

Macro catalog: **55 events** in `packages/data_system/config/events.csv` (CPI, NFP, PROP_FLATTEN — not CPI-only).

```bash
python packages/data_system/src/macro_event_cli.py              # list event_id
python packages/data_system/src/macro_event_cli.py --type NFP   # filter by type
```

```bash
# Download everything (macro imbalance enrich + estimate; pull only with --confirm-pull)
.\scripts\download_all_research_data.ps1
.\scripts\download_all_research_data.ps1 -ConfirmPull   # after approving estimate

# Full manifest-backed audit (macro + equities daily/normalized/options)
python scripts/audit_all_research_data.py

# Imbalance-only enrich (all 55 macro events + MBP-10 + auction NDJSON)
python scripts/download_imbalance_research_data.py --all --max-cost-usd 200

# Imbalance filename gaps only
python scripts/audit_imbalance_data_gaps.py

# Single macro event (any event_id from catalog)
python scripts/download_imbalance_research_data.py --event-id NFP_2024_01_05_TIGHT --symbol MES.v.0 --with-mbp10

# Full workbench campaign catalog (all periods for a model; slug from: python -m workbench list)
python apps/workbench/scripts/backfill_catalog.py --model SPREAD_BLOWOUT_RECOMPRESSION --symbol MES.v.0 --dry-run
python apps/workbench/scripts/backfill_catalog.py --model SPREAD_BLOWOUT_RECOMPRESSION --symbol MES.v.0 --download-missing --max-cost-usd 50

# Regenerate inventory
python scripts/build_imbalance_inventory.py

# Workbench run — --model slug + --event-id required (any catalog row)
python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id NFP_2024_01_05_TIGHT

# Full 8-mode imbalance ablation during run (slow; real per-mode replays)
python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id NFP_2024_01_05_TIGHT --imbalance-ablation-full

# Replay-backed ablation matrix (--model and --event-id required)
python -m workbench imbalance-ablation --model SPREAD_BLOWOUT_RECOMPRESSION --event-id NFP_2024_01_05_TIGHT

# Full 8-mode replay ablation
python -m workbench imbalance-ablation --model SPREAD_BLOWOUT_RECOMPRESSION --event-id NFP_2024_01_05_TIGHT --full

# Tests
python -m pytest tests/test_imbalance/ -q
```

## Artifacts

Each workbench run: `artifacts/research_cards/workbench_runs/<run_id>/imbalance/` (mirrored to `artifacts/runs/<run_id>/imbalance/`).

Key files: `imbalance_feature_manifest.json`, `imbalance_ablation_results.json`, `imbalance_quality_checks.json` (replay MBO/OF/book samples only), `auction_quality_checks.json` (auction feed QC, separate from replay), `true_vs_proxy_classification.json`, `imbalance_lineage.json`.

Auction imbalance is applied inside `ReplaySession` at each step (`sync_to_timestamp`) from `load_auction_events` — not post-hoc merged into replay samples.

## Promotion

Imbalance feature sets other than `none` require `imbalance_ablation_verdict` on the certification stamp. See `packages/features_engine/config/imbalance_features.yaml`.

## Limitations

- Auction imbalance ingest requires API pulls; inventory may show `unavailable` until data exists.
- C++ hot path does not yet include imbalance v1 slots; Python/catalog is authoritative for research.
- Options contract imbalance requires quote eligibility (`options_lane/src/imbalance_eligibility.py`).
