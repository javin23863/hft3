# Crypto replay data (honest labels)

Kraken/Binance raw captures live under `data/crypto/`. Bitfinex L3 MBO under `data/crypto/bitfinex_mbo_raw/`.
Replay NPZ is **gitignored** — generate locally after clone.

## Data classes

| Feed | Raw path | NPZ path | Classification | Promotion gate |
|------|----------|----------|----------------|----------------|
| **Bitfinex WS R0** | `data/crypto/bitfinex_mbo_raw/bitfinex_mbo_*.ndjson` | `data/replay/hftbacktest/crypto/bitfinex/{SYM}/*_mbo.npz` | **L3_VALIDATED** | **Required** for execution replay gate |
| Kraken WS `book` | `data/crypto/kraken_l3_raw/*.ndjson` | `data/replay/hftbacktest/crypto/kraken/{SYM}/*_depth.npz` | **L2_DEPTH_VALIDATED** | Diagnostic only |
| Binance diff depth | `data/crypto/binance_l2_raw/*.ndjson` | `data/replay/hftbacktest/crypto/binance/{sym}/*_l2.npz` | **L2_PROXY_ONLY** | Diagnostic only |

Bitfinex `prec=R0` is order-level MBO with native order IDs. Kraken `book` is aggregated depth with synthetic IDs — **not L3**.

## L3 pipeline pass criteria

To satisfy the workbench **execution replay gate** (not L2 depth/proxy):

1. **Source**: Bitfinex public WebSocket R0 only (`wss://api.bitfinex.com/ws/2`)
2. **NPZ path**: `data/replay/hftbacktest/crypto/bitfinex/BTC_USD/BTC_USD_mbo.npz` (and ETH_USD, SOL_USD)
3. **Meta sidecar** (`*_mbo.meta.json`):
   - `"data_class": "L3_MBO"`
   - `"execution_classification": "L3_VALIDATED"`
   - `"source_feed": "bitfinex_ws_r0"`
4. **Routing**: `resolve_validation_path` → `ExecutionCapability.L3_VALIDATED` for BTCUSDT/ETHUSDT/SOLUSDT
5. **Replay**: `validate_crypto_candidate` runs with `L3FifoQueueModel` (not SquareProb)

L2 depth/proxy rows are diagnostic only — they do **not** pass the promotion gate.

## Download L3 MBO (Bitfinex)

```bash
pip install -e .
python scripts/download_crypto_mbo.py              # record 512s + merge all raw + convert + verify
python scripts/download_crypto_mbo.py --duration 3600   # longer session
python scripts/download_crypto_mbo.py --convert-only    # re-merge existing raw without re-recording
python scripts/verify_crypto_replay_data.py
```

Re-run recording to accumulate history; `--merge-all` (default) merges every raw session into one NPZ per symbol.

## Verify replay loads (not a backtest)

```bash
python -m pytest tests/test_crypto_l2/test_bitfinex_mbo_converter.py -q
python -c "
from pathlib import Path
from research_pipeline.types import CandidateModel
from crypto_lane.src.validation.crypto_validation_workflow import validate_crypto_candidate
c = CandidateModel('t','CRYPTO_H1',{},'t', metadata={'symbol':'BTCUSDT'})
r = validate_crypto_candidate(c, Path('.'), max_steps=5000)
print(r.execution_classification, r.npz_path)
"
```

Empty raw files (<64 B) are skipped.
