# FEATURES.md — 64-Slot Feature Vector Capability Matrix

Version: 2026-06-10.
Source of truth: `packages/features_engine/src/features/feature_index.py`.
Implementation: `packages/features_engine/src/features/mbo_features.py`
(Python extractor) and `packages/decision_engine/cpp/include/decision_runtime.hpp`
(`std::array<double, 64>`).

Cross-asset is ON by default. `HFT3_CROSS_ASSET=0` disables cross-asset
hypothesis families 16–20 (ablation only).

Parity contract: `tests/test_cpp_feature_golden.py` gates any extractor change
(covers `REALIZED_VOL_STATE` slot 26 and regime slots 41–49).

---

## Slots 0–14: Flow and Book Shape

| Index | Name | Formula / source | Data dep | Lanes |
|-------|------|-----------------|----------|-------|
| 0 | AGGRESSOR_VOLUME_IMBALANCE | `(buy_agg - sell_agg) / total_agg` | single-sym MBO trades | CME, crypto |
| 1 | BUY_AGGRESSOR_VOLUME | running buy aggressor qty in window | single-sym MBO trades | CME, crypto |
| 2 | SELL_AGGRESSOR_VOLUME | running sell aggressor qty | single-sym MBO trades | CME, crypto |
| 3 | CANCEL_TO_ADD_RATIO | `cancel_vol / add_vol` | single-sym MBO adds/cancels | CME, crypto |
| 4 | NEAR_TOUCH_CANCEL_PRESSURE | near-touch (3-tick) cancel vol / add vol | single-sym MBO | CME, crypto |
| 5 | TOP_1_DEPTH_BID | bid qty at best | single-sym MBO book | CME, crypto |
| 6 | TOP_1_DEPTH_ASK | ask qty at best | single-sym MBO book | CME, crypto |
| 7 | TOP_3_DEPTH_BID | top-3 bid qty sum | single-sym MBO book | CME, crypto |
| 8 | TOP_3_DEPTH_ASK | top-3 ask qty sum | single-sym MBO book | CME, crypto |
| 9 | TOP_5_DEPTH_BID | top-5 bid qty sum | single-sym MBO book | CME, crypto |
| 10 | TOP_5_DEPTH_ASK | top-5 ask qty sum | single-sym MBO book | CME, crypto |
| 11 | TOP_10_DEPTH_BID | top-10 bid qty sum | single-sym MBO book | CME, crypto |
| 12 | TOP_10_DEPTH_ASK | top-10 ask qty sum | single-sym MBO book | CME, crypto |
| 13 | BOOK_SLOPE | `(b10 - a10) / (b10 + a10 + 1e-9)` | single-sym MBO book | CME, crypto |
| 14 | BOOK_SLOPE_CHANGE | delta of BOOK_SLOPE from prior event | single-sym MBO book | CME, crypto |

Top-K computed via `heapq.nlargest` / `heapq.nsmallest` (4 calls per
`top_k_depth` invocation; see HOT_PATH.md §3 for optimization roadmap).

---

## Slots 15–17: Spread Regime

| Index | Name | Notes | Data dep | Lanes |
|-------|------|-------|----------|-------|
| 15 | SPREAD | `best_ask - best_bid` | single-sym MBO book | CME, crypto |
| 16 | SPREAD_STRESS | `spread / median(spread_history[-100])` via `np.median(deque(100))` | single-sym MBO book | CME, crypto |
| 27 | SPREAD_STRESS_ELEVATED | `1.0` when `SPREAD_STRESS > 2.0`, else `0.0`; renamed from `IS_BREAKING_LEVEL` | single-sym MBO book | CME, crypto |

---

## Slots 18–25: Queue Position / Flow Toxicity Proxies

| Index | Name | Notes | Data dep | Lanes |
|-------|------|-------|----------|-------|
| 18 | QUEUE_DEPLETION_RATE_BID | `(prev_b1 - b1) / (prev_b1 + 1e-9)` | single-sym MBO book | CME, crypto |
| 19 | QUEUE_DEPLETION_RATE_ASK | analogous ask side | single-sym MBO book | CME, crypto |
| 20 | REFILL_RATIO | `add_vol / (cancel_vol + 1e-9)` | single-sym MBO | CME, crypto |
| 21 | ABSORPTION_SCORE | `hit_vol / (total_agg + 1e-9) * (1 - |slope|)` | single-sym MBO | CME, crypto |
| 22 | ICEBERG_RELOAD_SCORE | `tanh(reload_score)` from level reload × trade counts | single-sym MBO | CME, crypto |
| 23 | RELOAD_DROP_SCORE | drop in reload activity vs prior | single-sym MBO | CME, crypto |
| 24 | BID_ADD_CANCEL_RATIO | `bid_add_vol / (bid_cancel_vol + 1e-9)` | single-sym MBO | CME, crypto |
| 25 | ASK_ADD_CANCEL_RATIO | `ask_add_vol / (ask_cancel_vol + 1e-9)` | single-sym MBO | CME, crypto |

---

## Slot 17: Liquidity State

| Index | Name | Notes | Data dep | Lanes |
|-------|------|-------|----------|-------|
| 17 | LIQUIDITY_VACUUM_SCORE | `(prev_depth10 - depth10) / prev_depth10` (drop fraction) | single-sym MBO book | CME, crypto |

---

## Slot 26: Volatility

| Index | Name | Notes | Data dep | Lanes |
|-------|------|-------|----------|-------|
| 26 | REALIZED_VOL_STATE | `np.std(mid_returns[-100])` running std of tick returns | single-sym MBO book | CME, crypto |

---

## Slots 28–35: Session Levels and Context

| Index | Name | Notes | Data dep | Lanes |
|-------|------|-------|----------|-------|
| 28 | IS_BREAKING_SESSION_LEVEL | signed: +1 new high, -1 new low, 0 else | single-sym MBO mid | CME, crypto |
| 29 | DISTANCE_TO_ROUND_NUMBER | `dist_pts / tick_size`; default increment 10 pts for ES/MES | single-sym MBO mid | CME |
| 30 | DISTANCE_TO_VWAP | `(mid - trade_VWAP) / tick_size`; 0 until first TRADE event | single-sym MBO trades | CME, crypto |
| 31 | CUTOFF_PRESSURE_SCORE | prop/session flatten pressure | EventContextEngine | CME |
| 32 | PROP_REENTRY_SCORE | prop reopen window signal | EventContextEngine | CME |
| 33 | NEWS_RESTRICTION_FLATTEN_SCORE | economic event restriction flatten | EventContextEngine | CME |
| 34 | MAX_CONTRACT_TRADE_IMBALANCE | crowding in full-size contracts | single-sym MBO trades | CME |

(Slots 36–39: reserved / unassigned in current `FeatureIndex`.)

---

## Slot 40: Mid Price

| Index | Name | Notes |
|-------|------|-------|
| 40 | MID_PRICE | `(best_bid + best_ask) / 2`; 0 when BBO invalid |

---

## Slots 41–49: Regime Posterior

Source: `packages/features_engine/src/regime/regime_filter.py` (`RegimeFilter`).
9-label softmax posterior `P(Z_t | F_t)` from `RegimeFilter.update()`.
Written to vector by `market_state_pipeline.py`.

| Index | Regime label |
|-------|-------------|
| 41 | REGIME_NORMAL |
| 42 | REGIME_EVENT_SHOCK |
| 43 | REGIME_LIQUIDITY_VACUUM |
| 44 | REGIME_STOP_CASCADE |
| 45 | REGIME_PROP_FLATTEN |
| 46 | REGIME_BOOK_REBUILD |
| 47 | REGIME_CHOP |
| 48 | REGIME_TREND (trend_continuation) |
| 49 | REGIME_SPREAD_STRESS |

Event context (E_t) resolved by `EventContextEngine`
(`packages/features_engine/src/regime/event_context.py`) using
`packages/data_system/config/events.csv` windows; used as input to
`RegimeFilter.update()`.

---

## Slots 50–63: Structural Model Outputs

Source: `packages/features_engine/src/pipeline/structural_integration.py`
(`StructuralModelIntegrator`). `STRUCTURAL_FEATURE_START = 50`,
`STRUCTURAL_FEATURE_COUNT = 14`.

11 structural models from `packages/features_engine/src/structural_models/`
(registry: `registry.py`):

| Slot offset | Model ID | Model | Data dep |
|-------------|----------|-------|----------|
| 50 | BOOK_PRESSURE | OFI_value | single-sym MBO book |
| 51 | BOOK_PRESSURE | OFI_zscore | single-sym MBO book |
| 52 | VPIN_TOXICITY | VPIN_value | single-sym MBO trades |
| 53 | HYBRID_EXECUTION | hybrid_reservation_price | OFI + VPIN |
| 54 | DEALER_HEDGING | dealer_hedging_pressure | single-sym MBO mid |
| 55 | TRANSFER_ENTROPY | aggressive_liquidity_signal | price history |
| 56 | QUANTUM_SPREAD_DEFENSE | collapse_risk | spread ticks |
| 57–63 | remaining structural models | StochasticThermo, HawkesToxicFlow, DOW_YM_INDEX, TREASURY_CTD, CrossAssetLeadLag | various |

HAWKES_TOXIC_FLOW (model_11) requires `market_order_times` list.
DOW_YM_INDEX (model_06) and TREASURY_CTD (model_07) are single-symbol
analytical models (no external feed required at current implementation).
CROSS_ASSET_LEAD_LAG (model_02) uses `own_ofi` from BOOK_PRESSURE.

Model data dependency: multi-symbol MBO required for true cross-asset lead-lag;
current single-symbol integration passes `leader_ofi=own_ofi` as a placeholder.

---

## Cross-Asset Hypothesis Families (16–20)

Implemented as hypotheses, not feature slots. Source:
`packages/features_engine/src/hypotheses/registry.py`.

```python
CROSS_ASSET_HYP_IDS = frozenset({16, 17, 18, 19, 20})
```

Active by default. `HFT3_CROSS_ASSET=0` (or `false`/`no`) ablates them.
`get_active_hypotheses()` returns 45 by default, 40 under ablation.

These hypotheses (EsToMesLeadLag, NqToMnqLeadLag, EsNqDivergenceSnapback,
ZnZbToEsNqMacroImpulse, MicroContractRetailLag) require multi-symbol MBO feeds
in replay; they read `cross_asset_features` from `MarketState`.

---

## Python ↔ C++ Parity Contract

`tests/test_cpp_feature_golden.py` exercises the Python `MBOFeatureExtractor`
and `RegimeFilter` against a known event sequence and validates slots
`[26, 41..49]` (REALIZED_VOL_STATE + all regime posterior slots). Any change
to `mbo_features.py` or `regime_filter.py` that breaks golden output will fail
CI. The C++ binary `hft_feature_golden` is the counterpart; if built, the test
runs cross-language comparison.
