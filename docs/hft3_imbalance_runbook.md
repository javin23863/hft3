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

```bash
# Regenerate inventory
python scripts/build_imbalance_inventory.py

# Workbench run (writes imbalance/ artifacts under run dir)
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT

# Full 8-mode imbalance ablation during run (slow; real per-mode replays)
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT --imbalance-ablation-full

# Replay-backed ablation matrix (3-mode fast sweep by default)
python -m workbench imbalance-ablation --model HYP_5 --event-id CPI_2024_09_11_TIGHT

# Full 8-mode replay ablation
python -m workbench imbalance-ablation --model HYP_5 --event-id CPI_2024_09_11_TIGHT --full

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
