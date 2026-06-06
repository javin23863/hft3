# Crypto replay data (honest labels)

Kraken and Binance raw captures live under `data/crypto/`. Replay NPZ is **gitignored** — generate locally after clone.

## Data classes

| Feed | Raw path | NPZ path | Classification | Promotion gate |
|------|----------|----------|----------------|----------------|
| Kraken WS `book` | `data/crypto/kraken_l3_raw/*.ndjson` | `data/replay/hftbacktest/crypto/kraken/{SYM}/*_depth.npz` | **L2_DEPTH_VALIDATED** | Diagnostic only |
| Binance diff depth | `data/crypto/binance_l2_raw/*.ndjson` | `data/replay/hftbacktest/crypto/binance/{sym}/*_l2.npz` | **L2_PROXY_ONLY** | Diagnostic only |
| True order-level MBO | (not wired yet) | TBD | **L3_VALIDATED** | Required for execution replay gate |

Kraken ``book`` is aggregated price/qty depth with synthetic order IDs in the converter — **not** order-level MBO. Each NPZ has a sidecar `*.meta.json` with `"data_class": "L2_DEPTH"`.

## Setup (real recordings, no live fetch)

From repo root:

```bash
pip install -e .
python scripts/setup_crypto_replay_data.py
python scripts/verify_crypto_replay_data.py
```

Or manually:

```bash
python -m crypto_lane convert-l3 data/crypto/kraken_l3_raw/kraken_l3_BTC_USD_*.ndjson --routing-symbol BTC/USD
python -m crypto_lane convert-l2 data/crypto/binance_l2_raw/binance_l2_btcusdt_*.ndjson --routing-symbol BTCUSDT --no-fetch
```

## Verify replay loads (not a backtest)

```bash
python -m pytest tests/test_crypto_l2/test_crypto_execution_validator.py::test_run_crypto_replay_with_kraken_depth -q
```

Empty raw files (<64 B) are skipped by the setup script.
