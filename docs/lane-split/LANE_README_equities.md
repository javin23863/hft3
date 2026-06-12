# hft3-equities-lane

Stocks/equities research lane split out of `javin23863/hft3` at tag `pre-lane-split-20260612` (2026-06-12). Full snapshot of hft3 at that tag; pre-split history lives in hft3's git log.

## Identity (what this repo is FOR)
- `packages/equities_lane/` — low-float runner research: universe screener, ORB/consolidation patterns, OFI/VPIN/Hawkes/HMM features, L3 prediction stack, walk-forward backtest, OPRA options-chain ingest, IBKR Web API v2 endpoint (OAuth 1.0a / clientportal.gw)
- `tests/test_equities_lane/`, `scripts/{fetch_free_daily_ohlcv,fetch_delisted_daily,fetch_l2_l3_databento,fetch_runner_options_snapshots}.py`, `scripts/pull_equities_decadal.ps1`
- `docs/research/{PARABOLIC_LOW_FLOAT_DECADAL,LOW_FLOAT_RUNNER,EQUITY_OPTIONS_DATA_MAP}.md`
- Branches: `eqopt/live-probe` (IBKR OAuth probe), `stocks-lane-restored` (historical)

## NOT here
`packages/options_lane` (CME futures-options put/call parity) stayed in hft3 core — it trades GLBX, not OPRA equity options.

## TODO after split
1. **Secrets** (names only): IBKR OAuth vars per `packages/equities_lane/config/ibkr_endpoint.yaml`; quant-x env fallbacks (`%USERPROFILE%/.config/quant-x/keys.env`, `Documents/GitHub/quant-x/.env*`).
2. Trim the snapshot at leisure (CME dirs are hft3's).
3. Data: equities lake data stays in `C:\hft3-lake\{equities,options}` + B2 `Hft3repo/lake/...` (synced nightly).
