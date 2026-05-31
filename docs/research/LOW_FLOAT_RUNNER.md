# Low-float runner research lane

Authority: [low_float_momentum_anomaly_research_pack.pdf](../references/low_float_momentum_anomaly_research_pack.pdf) · Decadal matrix: [PARABOLIC_LOW_FLOAT_DECADAL.md](PARABOLIC_LOW_FLOAT_DECADAL.md)

Quarantined equities research lane for low-float momentum anomaly backtesting. **Does not** write to production CME `data/npz/` (BLUEPRINT §4).

## Package

Code: `packages/equities_lane/`

| Module | Role |
|--------|------|
| `src/ingest/` | Databento equities download, normalize, float metadata CSV |
| `src/screen/` | Config-driven universe filters (float, gap, RVOL, rotation) |
| `src/patterns/` | ORB and consolidation labels (no hard-coded entries) |
| `src/features/` | OFI/VPIN/Hawkes adapters + HMM regime + L3 stubs |
| `src/backtest/` | Execution sim, signal engine, walk-forward |
| `src/report/` | Experiment report with ablation and degraded assumptions |

## CLI

```bash
pip install -r packages/equities_lane/requirements.txt

python -m equities_lane.pipeline discover --config packages/equities_lane/config/universe.yaml

# Budget-gated Databento download (requires DATABENTO_API_KEY)
python -m equities_lane.pipeline download --symbol GME --date 2021-01-27

python -m equities_lane.pipeline normalize --raw data/equities/raw/<file>.dbn.zst

python -m equities_lane.pipeline screen --session packages/equities_lane/fixtures/low_float_session_v1.ndjson

python -m equities_lane.pipeline fixture-backtest

python -m equities_lane.pipeline experiment --config packages/equities_lane/config/universe.yaml --ablation all
```

## Decadal real-data pull (MBO L3 + OPRA options)

Requires `DATABENTO_API_KEY`. See [EQUITY_OPTIONS_DATA_MAP.md](EQUITY_OPTIONS_DATA_MAP.md) for options chain paths aligned to each equity session window.

```bash
python -m equities_lane.pipeline estimate-decadal

python -m equities_lane.pipeline pull-decadal --options-only --pull-options --override-operating-cap --resume
```

Manifest v2: `data/equities/manifest/session_bundle_v2.json`

**L3-only:** backtest, screen, and experiment refuse degraded (`mbp-1`) sessions. Real pulls use Databento `mbo` only. CI fixture uses `--allow-degraded` explicitly.

**Daily OHLCV:** 756 calendar days before each anomaly date (walk-forward train+val+test + RVOL). Refresh without re-downloading MBO:

```bash
python -m equities_lane.pipeline pull-decadal --refresh-daily --daily-only --override-operating-cap
```

```bash
python -m equities_lane.pipeline estimate-decadal

python -m equities_lane.pipeline pull-decadal --override-hard-limit --override-operating-cap --resume

# Or
powershell -File scripts/pull_equities_decadal.ps1
```

Real float metadata: `data/equities/metadata/float_pit.csv`. Manifest: `data/equities/manifest/decadal_pull.json`.

## Data paths (quarantined)

| Path | Purpose |
|------|---------|
| `data/equities/raw/` | Databento DBN downloads |
| `data/equities/daily/` | Databento ohlcv-1d parquet (756d lookback for walk-forward + RVOL) |
| `data/equities/metadata/float_pit.csv` | Point-in-time float (SEC-sourced) |
| `data/equities/manifest/` | Pull audit manifest |
| `data/equities/normalized/` | Lane NDJSON sessions |
| `data/replay/equities/` | Replay artifacts |
| `research_cards/equities/` | Experiment reports |

## Degraded mode

**Production research is L3-only** (`l3_only: true` in `universe.yaml`). Backtest/screen/experiment raise on `degraded_mode: true`.

The CI synthetic fixture (`low_float_session_v1.ndjson`) remains degraded for unit tests via `--allow-degraded`. Do not use degraded tape for real runner research.

## Walk-forward

Walk-forward fold generation and float metadata lookups enforce event-time filtration (`as_of_date <= session_date`). Pattern labels in the backtester are recomputed on `ticks[:i+1]` at each decision tick. Full multi-session OOS fold execution is v1 scaffold — use `experiment` for in-session ablation until batch orchestration lands.

## Non-goals (v1)

- Workbench campaign adapter
- Live trading / CHI404 hot path
- Full 10-year batch orchestration (operational runbook only)
