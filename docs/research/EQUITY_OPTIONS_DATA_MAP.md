# Equity-linked OPRA options chains (time-aligned to decadal equity sessions)

| Path | Contents |
|------|----------|
| `data/options/equity_chains/raw/{session_id}/` | Databento DBN downloads |
| `data/options/equity_chains/normalized/{session_id}.ndjson` | Quote records per session |
| `data/equities/manifest/session_bundle_v2.json` | Factual join manifest |

## Window join

Each manifest row shares:

- `session_id` — catalog key (e.g. `gme_2021`)
- `window_start_utc` / `window_end_utc` — same ET bounds as equity MBO (`premarket_start` → `session_end`)

## Normalized options NDJSON fields

| Field | Description |
|-------|-------------|
| `session_id` | Catalog session id |
| `underlying` | Equity ticker |
| `quote_ts_ns` | Exchange timestamp (ns) |
| `symbol` | OPRA symbol |
| `strike` | Strike price |
| `right` | Call/put class |
| `expiry` | Expiration date |
| `bid` / `ask` | Quote |

## Equity side (existing)

| Path | Contents |
|------|----------|
| `data/equities/raw/{SYMBOL}_{DATE}_mbo.dbn.zst` | L3 MBO |
| `data/equities/normalized/{SYMBOL}_{DATE}.ndjson` | Normalized equity tape + meta |
| `data/equities/daily/{SYMBOL}.parquet` | 756d daily OHLCV |

## Pull commands

```bash
python -m equities_lane.pipeline estimate-decadal
python -m equities_lane.pipeline pull-decadal --options-only --pull-options --override-operating-cap --resume
python -m equities_lane.pipeline pull-decadal --pull-options --session-id gme_2021 --override-operating-cap
```

Catalog: [`packages/equities_lane/config/decadal_runners.yaml`](../../packages/equities_lane/config/decadal_runners.yaml)

Research compare (equity vs options vs hedge) is downstream of these artifacts.
