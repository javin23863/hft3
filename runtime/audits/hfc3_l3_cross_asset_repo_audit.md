# HFC3 Level-3 cross-asset repo audit

Generated: 2026-05-30T15:51:25.885978+00:00

## Summary

- **events.csv rows:** 56
- **NPZ files:** 52
- **Manifest downloads:** 52 ($1.1509)

## Audit answers

### 1 symbols with event windows
- ES.v.0
- MES.v.0
- MNQ.v.0
- NQ.v.0
- ZB.v.0
- ZN.v.0

### 2 symbols with mbo downloads
- **ES.v.0:** 8
- **MES.v.0:** 44

### 3 symbols with npz conversion
- **ES.v.0:** 8
- **MES.v.0:** 44

### 4 symbols hftbacktest replay ready
- **ES.v.0:** 8
- **MES.v.0:** 44

### 5 event types supported
- CPI
- NFP
- PROP_FLATTEN_TOPSTEP

### 6 event types missing
- PPI
- CORE_PPI
- PCE
- CORE_PCE
- UNEMPLOYMENT_CLAIMS
- FOMC_STATEMENT
- FOMC_PRESS
- FOMC_MINUTES
- FED_SPEAKER
- TREASURY_AUCTION
- TREASURY_REFUNDING
- EIA_CRUDE
- EIA_NATGAS
- USDA_WASDE
- USDA_CROP
- CASH_EQUITY_OPEN
- FUTURES_ROLL
- FUTURES_EXPIRY
- OPTIONS_EXPIRY
- PROP_REOPEN
- FRIDAY_CLOSE

### 7 true mbo derived features
- MBOFeatureExtractor 64-dim vector slots 0-26 (aggressor, depth, spread, queue, iceberg, vol)
- OrderBook L3 apply_event ADD/CANCEL/MODIFY/TRADE
- Regime slots 41-49 from RegimeFilter posterior

### 8 cross asset placeholders only
- MarketState.cross_asset_features dict — empty in pipeline
- FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE slot 34 — unpopulated
- HYP 16-20 cross-asset hypotheses gated by HFT3_CROSS_ASSET env

### 9 single instrument code paths
- features_engine/src/features/mbo_features.py:114 — one OrderBook per MBOFeatureExtractor
- features_engine/src/pipeline/market_state_pipeline.py:26 — one MBOFeatureExtractor
- MBOEvent has no symbol field — single-stream assumption

### 10 es mes only assumptions
- backtest_pipeline/src/runner.py:30,50 — default product=MES, single BacktestAsset
- backtest_pipeline/src/signal_backtester.py — TICK_VALUE_MES default
- features_engine/src/structural_models/model_02_cross_asset_lead_lag.py — leader ES target MES
- workbench default symbol MES.v.0; ES.v.0 fallback for pre-2019 Discovery only

### 11 cpi nfp only assumptions
- data_system/config/events.csv — CPI×19, NFP×33, PROP×4 only
- features_engine/src/regime/event_context.py:52-55 — explicit CPI_TIGHT/NFP_TIGHT
- No FOMC or cash-open rows in events.csv (labels exist in event_context only)

### 12 multi symbol mbo tensor changes needed
- backtest/adapters/rithmic_replay_loader.py — resolve_event_npz single path per event
- backtest_pipeline/src/converter.py — one symbol per NPZ file
- No runtime/event_snapshots/ multi-symbol tensor builder existed before hfc3/
- ReplayRunner accepts one data_path only

## Code path assumptions

### single_order_book
- features_engine/src/features/mbo_features.py:114 — one OrderBook per MBOFeatureExtractor
- features_engine/src/pipeline/market_state_pipeline.py:26 — one MBOFeatureExtractor
- MBOEvent has no symbol field — single-stream assumption

### cross_asset_placeholder
- features_engine/src/pipeline/market_state_pipeline.py:31,67 — cross_asset_features never populated
- backtest_pipeline/src/hft_strategy.py — cross_asset_features={} in depth fallback
- features_engine/src/hypotheses/modules.py:401+ — HYP 16-20 expect ES/NQ/ZN keys

### es_mes_defaults
- backtest_pipeline/src/runner.py:30,50 — default product=MES, single BacktestAsset
- backtest_pipeline/src/signal_backtester.py — TICK_VALUE_MES default
- features_engine/src/structural_models/model_02_cross_asset_lead_lag.py — leader ES target MES
- workbench default symbol MES.v.0; ES.v.0 fallback for pre-2019 Discovery only

### cpi_nfp_focus
- data_system/config/events.csv — CPI×19, NFP×33, PROP×4 only
- features_engine/src/regime/event_context.py:52-55 — explicit CPI_TIGHT/NFP_TIGHT
- No FOMC or cash-open rows in events.csv (labels exist in event_context only)

### mbo_canonical
- data_system/src/databento_client.py:28 — schema=mbo GLBX.MDP3
- features_engine/src/features/npz_feed.py — HftBacktest structured MBO array
- No L1/L2 download path in databento_client (correct for this stack)

### multi_symbol_tensor_gaps
- backtest/adapters/rithmic_replay_loader.py — resolve_event_npz single path per event
- backtest_pipeline/src/converter.py — one symbol per NPZ file
- No runtime/event_snapshots/ multi-symbol tensor builder existed before hfc3/
- ReplayRunner accepts one data_path only

## Required changes for multi-symbol MBO event tensors

1. Load one NPZ per instrument per event_id (not single MES path).
2. Build per-symbol OrderBook state at anchor offsets T±{300..1}s.
3. Populate `cross_asset_features` from MBO-derived state (not L1/L2 quotes).
4. Extend `events.csv` / calendar for FOMC, rates, energy, USDA, etc.
5. Mark VIX/VVIX as SENSOR_ONLY — never force into MBO schema.
6. Wrapper replay: primary execution instrument + cross-asset feature feed.

See `hfc3/events/l3_event_snapshot_tensor.py` and `hfc3/features/cross_asset_l3_event_features.py`.