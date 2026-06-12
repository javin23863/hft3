# hft3-crypto-lane

Crypto research lane split out of `javin23863/hft3` at tag `pre-lane-split-20260612` (2026-06-12). This repo is a full snapshot of hft3 at that tag — the crypto lane plus every shared package it imports (data_system, backtest_pipeline, execution, validation framework). Pre-split history lives in hft3's git log.

## Identity (what this repo is FOR)
- `packages/crypto_lane/` — pipeline, ingest (B2 bronze), venue recorders (binance L2, bitfinex MBO, coinbase MBO, kraken L3), features/labels/ml, candidates h1–h7
- `packages/crypto_lane/edge_daemon/` — Rust BTC mempool edge daemon (ZMQ → Chicago receiver)
- `infrastructure/crypto_lane/` — btc-edge-daemon/receiver systemd units
- `packages/execution/{adapters/crypto_broker.py,crypto_risk.py,crypto_paper_harness.py}`, `packages/backtest_pipeline/src/{crypto_hft_builder,crypto_latency}.py`
- `tests/test_crypto_lane/`, `tests/test_crypto_l2/`, root `tests/test_crypto_*.py`
- `backtests/configs/crypto_hypotheses/`, `specs/{ALPHA_CRYPTO,CRYPTO_LIVE}.md`, `docs/architecture/BITCOIN_EDGE_*`
- Latest WIP carried in: crypto_broker reject-reason capture (commit b5a895f)

## TODO after split
1. **Re-point BTC-VPS deploy units**: `infrastructure/crypto_lane/btc-edge-receiver-run` hardcodes `cd /root/hft3/repo` — clone THIS repo on the VPS and update paths.
2. **Secrets** (names only; values live in env files): `HFT3_CRYPTO_B2_KEY_ID/APP_KEY/BUCKET/SOURCE_BUCKET/ENDPOINT`, Kraken keys, `/root/.cae-rpc.env` on the VPS.
3. Trim the snapshot: CME-only dirs (engine/, rithmic_gateway/, apps/cockpit, …) can be deleted here at leisure — they're hft3's.
4. Data: crypto lake data stays in `C:\hft3-lake\crypto` + B2 `Hft3repo/lake/crypto` (synced nightly from the workstation).
