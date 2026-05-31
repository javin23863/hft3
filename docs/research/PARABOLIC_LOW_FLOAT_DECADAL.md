# Parabolic low-float decadal research authority

Companion to [low_float_momentum_anomaly_research_pack.pdf](../references/low_float_momentum_anomaly_research_pack.pdf).

This document captures the expanded decadal retrospective hypothesis: parabolic low-float equities driven by supply constraints (sub-15M float), short interest, thematic catalysts, and L3 microstructure signals (OFI, Hawkes supercriticality), with dilution overhang (S-3/ATM) as exclusion criteria.

## Pull authority

Real-data pulls use [`packages/equities_lane/config/decadal_runners.yaml`](../../packages/equities_lane/config/decadal_runners.yaml) — 16 anomaly sessions (2016–2026).

Point-in-time float: [`data/equities/metadata/float_pit.csv`](../../data/equities/metadata/float_pit.csv) (SEC EDGAR CIK references).

## Decadal matrix (summary)

| id | symbol | date | catalyst |
|----|--------|------|----------|
| drys_2016 | DRYS | 2016-11-11 | shipping squeeze |
| lfin_2017 | LFIN | 2017-12-18 | blockchain acquisition |
| lbcc_2017 | LBCC | 2017-12-21 | blockchain rebrand |
| kodk_2020 | KODK | 2020-07-28 | govt loan |
| spi_2020 | SPI | 2020-09-23 | EV subsidiary |
| gme_2021 | GME | 2021-01-27 | meme squeeze |
| expr_2021 | EXPR | 2021-01-27 | sympathy squeeze |
| indo_2022 | INDO | 2022-03-07 | oil shock |
| hkd_2022 | HKD | 2022-08-02 | IPO lockup |
| top_2023 | TOP | 2023-04-27 | retail squeeze |
| holo_2024 | HOLO | 2024-02-07 | AI squeeze |
| cycc_2025 | CYCC | 2025-07-15 | biotech momentum |
| aire_2025 | AIRE | 2025-07-10 | AI surge |
| bird_2026 | BIRD | 2026-04-15 | AI pivot |
| amst_2026 | AMST | 2026-05-12 | AI nursing |
| snal_2026 | SNAL | 2026-05-08 | ATM dilution (negative control) |

## Pull commands (no backtest)

```bash
python -m equities_lane.pipeline estimate-decadal

python -m equities_lane.pipeline pull-decadal --override-hard-limit --override-operating-cap --resume
```

Or: `scripts/pull_equities_decadal.ps1`

Output manifest: `data/equities/manifest/decadal_pull.json`

## Data paths

| Layer | Path |
|-------|------|
| MBO raw | `data/equities/raw/` |
| Daily OHLCV | `data/equities/daily/{symbol}.parquet` |
| Normalized | `data/equities/normalized/` |
| Float PIT | `data/equities/metadata/float_pit.csv` |

See [LOW_FLOAT_RUNNER.md](LOW_FLOAT_RUNNER.md) for lane architecture.
